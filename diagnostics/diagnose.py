#!/usr/bin/env python3
"""Local Ergonaut HRM recorder/analyzer. Python 3 stdlib; never flashes hardware."""
import argparse
import bisect
import collections
import glob
import json
import os
from pathlib import Path
import re
import select
import sys
import time

EN = ['`','q','w','e','r','t','y','u','i','o','p','[',']','a','s','d','f','g','h','j','k','l',';',"'",'-','z','x','c','v','b','n','m',',','.','/','\\','Tab','Space','Esc','Enter','Space','Backspace']
RU = ['ё','й','ц','у','к','е','н','г','ш','щ','з','х','ъ','ф','ы','в','а','п','р','о','л','д','ж','э','-','я','ч','с','м','и','т','ь','б','ю','.','\\','Tab','Space','Esc','Enter','Space','Backspace']
HRM = {13:'Ctrl',14:'Alt',15:'Cmd',16:'Shift',19:'Shift',20:'Cmd',21:'Alt',22:'Ctrl'}
# Standard touch typing. Labels describe positions, not inferred OS input.
FINGERS = [0,0,1,2,3,3,4,4,5,6,7,7,0,0,1,2,3,3,4,4,5,6,7,7,0,0,1,2,3,3,4,4,5,6,7,7,8,8,8,9,9,9]
P = re.compile(r'HD1 P (\d+) (\d+) (\d+) ([01]) (\d+)')
D = re.compile(r'HD1 D (\d+) (\d+) (\d+) (tap|hold-interrupt|hold-timer) ([a-z-]+)')
ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def label(pos):
    if 0 <= pos < len(EN):
        return f'{EN[pos]}/{RU[pos]} [#{pos}]'
    return f'позиция #{pos}'

def hand(pos):
    if pos < 36: return 'L' if pos % 12 < 6 else 'R'
    return 'L' if pos < 39 else 'R'

def analyze(text):
    """Replay received position events; never reconstruct physical times from HID."""
    strokes, decisions, warnings = [], [], []
    active = {}
    session, last_seq, last_down = 0, None, None
    p_count = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        line = ANSI.sub('', line)
        p, d = P.search(line), D.search(line)
        if p:
            seq, ts, pos, state, source = map(int, p.groups())
            if last_seq is not None and seq <= last_seq:
                warnings.append(f'Строка {line_no}: новая сессия/сброс счётчика; интервалы разделены.')
                session += 1
                active.clear()
                last_down = None
            elif last_seq is not None and seq != last_seq + 1:
                warnings.append(f'Строка {line_no}: пропуск событий {last_seq} → {seq}; выводы рядом неполные.')
            last_seq = seq
            p_count += 1
            key = (session, pos)
            if not 0 <= pos < 42:
                warnings.append(f'Строка {line_no}: неизвестная позиция {pos}.')
            if state:
                if key in active:
                    warnings.append(f'Повторный down без up: {label(pos)}; интервал неполный.')
                s = dict(session=session, pos=pos, down=ts, up=None, source=source,
                         gap=None if last_down is None else ts-last_down, decision=None)
                strokes.append(s)
                active[key] = s
                last_down = ts
            else:
                s = active.pop(key, None)
                if s is None:
                    warnings.append(f'Отпускание без нажатия: {label(pos)} (возможно, запись началась позже).')
                else:
                    s['up'] = ts
                    if ts < s['down']: warnings.append('Немонотонное время: не интерпретируйте этот интервал.')
        elif d:
            ts, down, pos = map(int, d.groups()[:3])
            decisions.append(dict(session=session, at=ts, down=down, pos=pos,
                                  status=d[4], moment=d[5]))
    # Decisions can precede raw ups; associate after parsing by firmware press timestamp.
    by_press = collections.defaultdict(list)
    for s in strokes: by_press[(s['session'],s['pos'],s['down'])].append(s)
    for d in decisions:
        candidates = by_press.get((d['session'],d['pos'],d['down']), [])
        if len(candidates) == 1: candidates[0]['decision'] = d
        else: warnings.append(f"Решение для {label(d['pos'])} не удалось однозначно связать с нажатием.")
    incomplete = sum(s['up'] is None for s in strokes)
    if incomplete: warnings.append(f'Незавершённых нажатий: {incomplete}; возможно, запись оборвалась.')
    if not p_count: warnings.append('Нет физических событий HD1 P. Обычный HID/стандартный лог не заменяет диагностическую прошивку.')
    if not decisions: warnings.append('Нет решений HD1 D: причины выбора hold/tap не подтверждены прошивкой.')
    pairs = []
    sessions = collections.defaultdict(list)
    for s in strokes: sessions[s["session"]].append(s)
    for group in sessions.values(): group.sort(key=lambda s: s["down"])
    times = {k:[s["down"] for s in v] for k,v in sessions.items()}
    for a in strokes:
        if a['pos'] not in HRM or a['up'] is None: continue
        group = sessions[a['session']]
        start = bisect.bisect_right(times[a['session']], a['down'])
        for i in range(start, len(group)):
            b = group[i]
            if b['down'] >= a['up']: break
            if b['up'] is None: continue
            if not (0 <= a['pos'] < 42 and 0 <= b['pos'] < 42): continue
            if FINGERS[a['pos']] == FINGERS[b['pos']]: continue
            pairs.append(dict(first=a['pos'], second=b['pos'], at=a['down'],
                session=a['session'], kind='nested' if b['up'] < a['up'] else 'roll',
                hands='same' if hand(a['pos']) == hand(b['pos']) else 'cross',
                held_ms=a['up']-a['down'], gap_before_ms=a['gap'],
                second_down_ms=b['down']-a['down'], second_up_ms=b['up']-a['down'],
                first_up_ms=a['up']-a['down'], decision=a['decision']))
    return dict(version=1, position_events=p_count, strokes=strokes, decisions=decisions,
                pairs=pairs, warnings=list(dict.fromkeys(warnings)))

def render(r):
    out = [f"Физических событий: {r['position_events']}; нажатий: {len(r['strokes'])}; решений HRM/слоёв: {len(r['decisions'])}.",
           'Подписи EN/RU — позиции вашей раскладки; язык набора из лога неизвестен.',
           'Время правой половинки — как события видит центральная половинка, с влиянием split-связи.',
           'Hold подтверждает модификатор, но не доказывает ошибку: намерение из лога неизвестно.']
    for w in r['warnings']: out.append('ВНИМАНИЕ: '+w)
    counts = collections.Counter((p['hands'],p['kind']) for p in r['pairs'])
    out += ['', 'Перекрытия после HRM (разные пальцы):']
    for hands, title in [('same','Одна рука'),('cross','Разные руки')]:
        out.append(f"  {title}: перекатов {counts[hands,'roll']}, вложенных {counts[hands,'nested']}.")
    holds = [s for s in r['strokes'] if s['pos'] in HRM and s['decision'] and s['decision']['status'].startswith('hold')]
    out += ['',f'Подтверждённых включений HRM-модификатора: {len(holds)}']
    for s in holds:
        d = s['decision']
        desc = {'other-key-up':'отпускание другой клавиши','timer':'истечение порога',
                'other-key-down':'нажатие другой клавиши'}.get(d['moment'],d['moment'])
        dur = '?' if s['up'] is None else str(s['up']-s['down'])
        out.append(f"  Сессия {s['session']+1}, {s['down']} мс: {label(s['pos'])} → {HRM[s['pos']]}; {desc}; удержание {dur} мс.")
        related = [p for p in r['pairs'] if p['session']==s['session'] and p['at']==s['down'] and p['first']==s['pos']]
        for p in related:
            out.append(f"    {label(p['second'])}: down +{p['second_down_ms']}, up +{p['second_up_ms']} мс; "
                       f"{'вложенное' if p['kind']=='nested' else 'перекат'}, {'между руками' if p['hands']=='cross' else 'одной рукой'}.")
    out += ['', 'Это наблюдение текущего поведения, а не симуляция другой настройки.',
            'Для поиска именно ошибок приложите набранный текст и отметьте намеренные хоткеи.']
    return '\n'.join(out)+'\n'

def ports():
    return sorted(set(glob.glob('/dev/cu.usbmodem*')+glob.glob('/dev/ttyACM*')))

def capture(port, output, seconds):
    import termios
    import tty
    # Exclusive output prevents overwriting a previous experiment. This is a serial device, not a UF2 disk.
    if not (port.startswith('/dev/cu.usbmodem') or port.startswith('/dev/ttyACM')):
        raise ValueError('Укажите USB CDC порт из команды ports, а не диск загрузчика.')
    with open(output,'xb',buffering=0) as f:
        fd = os.open(port,os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        original = None
        try:
            original = termios.tcgetattr(fd)
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = attrs[5] = termios.B115200
            attrs[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd,termios.TCSANOW,attrs)
            # CDC clients commonly assert DTR to let the device emit logs.
            try:
                import fcntl, struct
                fcntl.ioctl(fd,termios.TIOCMBIS,struct.pack('I',termios.TIOCM_DTR))
            except OSError: pass
            f.write(('# Ergonaut diagnostics '+time.strftime('%Y-%m-%dT%H:%M:%S%z')+'\n').encode())
            print(f'Записываю {port} → {output}\nНабирайте в текстовом редакторе. Ctrl+C здесь останавливает запись.\nЛог содержит последовательность клавиш: не набирайте пароли во время записи.',flush=True)
            start = time.monotonic()
            received = 0
            while not seconds or time.monotonic()-start < seconds:
                ready,_,_ = select.select([fd],[],[],0.2)
                if ready:
                    data = os.read(fd,65536)
                    if not data: break
                    f.write(data)
                    received += len(data)
            print(f'Запись завершена: {received} байт.')
        except KeyboardInterrupt:
            print('\nЗапись остановлена, файл сохранён.')
        finally:
            if original is not None:
                try: termios.tcsetattr(fd,termios.TCSANOW,original)
                except OSError: pass
            os.close(fd)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('ports',help='Показать USB serial порты')
    cap=sub.add_parser('record',help='Записать USB лог в новый файл')
    cap.add_argument('--port',required=True)
    cap.add_argument('--out',required=True)
    cap.add_argument('--seconds',type=float,default=0)
    ana=sub.add_parser('analyze',help='Разобрать запись')
    ana.add_argument('log')
    ana.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='ports': print('\n'.join(ports()) or 'USB CDC портов не найдено. Нужна диагностическая прошивка и USB к левой половинке.')
        elif args.command=='record':
            if args.seconds < 0: raise ValueError('--seconds должен быть неотрицательным')
            capture(args.port,args.out,args.seconds)
        else:
            result=analyze(Path(args.log).read_text(errors='replace'))
            print(json.dumps(result,ensure_ascii=False,indent=2) if args.json else render(result),end='\n' if args.json else '')
            if not result['position_events']: return 2
    except (OSError,ValueError) as e:
        print(str(e),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': sys.exit(main())
