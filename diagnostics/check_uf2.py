#!/usr/bin/env python3
"""Read-only image gate. Never copies to a device."""
import hashlib,json,struct,sys
from pathlib import Path

def check(path):
    data=Path(path).read_bytes()
    if not data or len(data)%512: raise ValueError('Truncated/empty UF2')
    addresses=[];numbers=[];families=set();totals=set()
    for off in range(0,len(data),512):
        b=data[off:off+512]
        m0,m1,flags,addr,size,idx,total,fam=struct.unpack_from('<8I',b)
        if (m0,m1,struct.unpack_from('<I',b,508)[0])!=(0x0a324655,0x9e5d5157,0x0ab16f30): raise ValueError('Bad magic')
        if flags!=0x2000 or fam!=0xada52840 or size!=256: raise ValueError('Not nRF52840 application UF2')
        # The observed Seeed 0.6.1 CURRENT spans 0x1000..0xea000. Never write SD,
        # bootloader, UICR, or the top application-data area for this task.
        if addr<0x27000 or addr+size>0xea000 or addr%256: raise ValueError(f'Unsafe address {addr:#x}')
        addresses.append(addr);numbers.append(idx);families.add(fam);totals.add(total)
    n=len(addresses)
    if sorted(numbers)!=list(range(n)) or totals!={n} or len(set(addresses))!=n: raise ValueError('Missing/duplicate blocks')
    return dict(name=Path(path).name,bytes=len(data),blocks=n,min_address=hex(min(addresses)),
                end_address=hex(max(addresses)+256),sha256=hashlib.sha256(data).hexdigest())

if __name__=='__main__':
    print(json.dumps([check(p) for p in sys.argv[1:]],indent=2))
