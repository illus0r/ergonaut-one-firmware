#!/usr/bin/env python3
"""Prepare an OFFLINE diagnostic source tree. Does not build, upload or flash."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ZMK_REV='4dd95d7840eb3d5a3f59bb29fbf1ab054715ef85'
CONFIG_REV='80e5a64986ca48389d69550992e29069cccad92b'


def revision(path):
    return subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()


def patch_sources(root):
    event=root/'app/src/event_manager.c'
    ht=root/'app/src/behaviors/behavior_hold_tap.c'
    old=event.read_text()
    include='#include <zmk/event_manager.h>'
    target='int zmk_event_manager_raise(zmk_event_t *event) { return zmk_event_manager_handle_from(event, 0); }'
    if old.count(target)!=1 or old.count(include)!=1:
        raise ValueError('event_manager.c отличается от проверенной структуры; патч не применён.')
    new=old.replace(include,include+'\n#include <zmk/events/position_state_changed.h>')
    new=new.replace(target,'''/* HD1: observation only, before any behavior captures/replays the event. */
int zmk_event_manager_raise(zmk_event_t *event) {
    static uint32_t hd_sequence;
    const struct zmk_position_state_changed *hd = as_zmk_position_state_changed(event);
    if (hd != NULL) {
        LOG_INF("HD1 P %u %lld %u %u %u", ++hd_sequence,
                (long long)hd->timestamp, hd->position, (unsigned int)hd->state,
                (unsigned int)hd->source);
    }
    return zmk_event_manager_handle_from(event, 0);
}''')
    text=ht.read_text()
    marker='''    LOG_DBG("%d decided %s (%s decision moment %s)", hold_tap->position,'''
    if text.count(marker)!=1: raise ValueError('hold_tap.c отличается; патч не применён.')
    text=text.replace(marker,'''    /* The original decision and behavior remain unchanged. */
    LOG_INF("HD1 D %lld %lld %u %s %s", (long long)k_uptime_get(),
            (long long)hold_tap->timestamp, (unsigned int)hold_tap->position,
            status_str(hold_tap->status), decision_moment_str(decision_moment));
'''+marker)
    event.write_text(new)
    ht.write_text(text)


def main():
    p=argparse.ArgumentParser(description="Instrument pinned ZMK source; no hardware access")
    p.add_argument("zmk",type=Path)
    a=p.parse_args()
    expected="3f7c9d7cc4f46617faad288421025ea2a6b0bd28"
    if revision(a.zmk)!=expected:
        p.error("Unexpected ZMK revision; refusing to patch")
    patch_sources(a.zmk)
    print("HD1 observation hooks added; behavior logic unchanged")

if __name__=="__main__": main()
