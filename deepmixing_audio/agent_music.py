"""Chat access to the existing selected-region guide workflow, without implicit DSP."""
import copy
from .models import number


def update_guides(tools,args):
    if tools.kind!='pair' or not tools.project:raise ValueError('请先建立双轨工程，再检查或校正拍网')
    p=copy.deepcopy(tools.project);action=args['action']
    if action=='inspect':
        report=tools.local_call(tools.service.inspect_music,p);guides={}
        for s in ('a','b'):
            r=report[s];old=p.get('musical_guides',{}).get(s)
            g=dict(track_id=p[s]['track_id'],start=r['start'],end=r['end'],bpm=r['grid']['bpm'],anchor=r['grid']['anchor'],
                   marker=r['grid']['anchor'],key_tonic=r['key']['tonic'],key_mode=r['key']['mode'],key_source=r['key']['status'])
            if old and old['track_id']==g['track_id']:
                if g['start']<=old['marker']<=g['end']:g['marker']=old['marker']
                if abs(old['start']-g['start'])<.00001 and abs(old['end']-g['end'])<.00001:
                    for k in ('bpm','anchor','key_tonic','key_mode','key_source','bar_anchor','beats_per_bar'):
                        if k in old:g[k]=old[k]
            guides[s]=g
        p['musical_guides']=guides
        return tools.commit(p,'已读取当前选段开头最多60秒的候选拍网与局部调性，保留同一区域的手动校正。仅更新标记，未自动对速或移调；强拍、乐句和调性需试听确认。')
    gs=p.get('musical_guides',{})
    if not all(s in gs and gs[s]['track_id']==p[s]['track_id'] and gs[s]['start']<=p[s]['start']<=gs[s]['end'] and gs[s]['end']>=min(p[s]['end'],p[s]['start']+60)-.002 for s in ('a','b')):
        raise ValueError('音乐标记缺失或选段已改变，请先重新检查当前选段')
    if action in ('calibrate','marker','bar','key'):
        s=args.get('slot')
        if s not in ('a','b'):raise ValueError('请指定A或B轨')
        g=gs[s]
        if action=='calibrate':
            first=number(args.get('first'),'首拍位置',g['start'],g['end'])
            last=number(args.get('last'),'后拍位置',g['start'],g['end'])
            intervals=args.get('intervals')
            if isinstance(intervals,bool) or not isinstance(intervals,int) or not 1<=intervals<=128 or last<=first:raise ValueError('请用有效的两个拍点和相隔1–128拍校正')
            g.update(bpm=number(round(60*intervals/(last-first),3),'校正BPM',40,240),anchor=first)
        elif action=='marker':g['marker']=number(args.get('time'),'切入标记',max(g['start'],p[s]['start']),min(g['end'],p[s]['end']))
        elif action=='bar':g.update(bar_anchor=g['marker'],beats_per_bar=4)
        else:
            tonic=args.get('tonic');mode=args.get('mode')
            if isinstance(tonic,bool) or not isinstance(tonic,int) or not 0<=tonic<=11 or mode not in ('major','minor'):raise ValueError('请指定C=0到B=11的主音及大小调')
            g.update(key_tonic=tonic,key_mode=mode,key_source='user')
        reason='已按你的指定更新音乐标记；尚未改变声音。对速或切入可以继续告诉我。'
    elif action=='align':
        if p['mode']!='mashup':raise ValueError('按两处标记切入适用于人声与伴奏叠搭')
        speed=p['target_bpm']/p['a']['bpm'] if p['target_bpm'] else 1
        offset=(gs['a']['marker']-p['a']['start'])/speed
        if gs['a']['marker']>p['a']['end'] or gs['b']['marker']<p['b']['start'] or p['b']['end']-gs['b']['marker']<.25:raise ValueError('切入标记超出当前选段或伴奏剩余不足0.25秒')
        p['b_offset_seconds']=number(offset,'伴奏延后',0,60);p['b']['start']=gs['b']['marker']
        reason='已将伴奏标记对齐人声标记，考虑当前速度换算；重新试听确认切入。'
    elif action=='tempo':
        if not all(gs[s]['bpm'] for s in ('a','b')):raise ValueError('请先确认两轨BPM')
        p['target_bpm']=gs['b']['bpm']
        for s in ('a','b'):p[s]['bpm']=gs[s]['bpm']
        reason='已按你的对速请求以B轨为目标速度；变速可能影响瞬态和尾音，需重新试听。'
    else:raise ValueError('不支持的音乐标记操作')
    return tools.commit(p,reason)
