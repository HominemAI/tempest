"""Grounded verbal synthesis of already sampled anchors; no extra inference."""
import hashlib
from pathlib import Path

VERSION='1.0.0'
# The modifier supplies the direction. No clause asserts an action or its cause.
CLAUSES={
 'self_preservation':'protecting their own safety feels {amount} important',
 'threat_vigilance':'their attention is {amount} drawn toward possible danger',
 'mobilization':'they feel {amount} ready to act',
 'alarm':'they feel {amount} immediate apprehension and urgency',
 'aversion':'they feel {amount} pressure to turn away from what is unwanted',
 'anger_resistance':'they feel {amount} pressure to push back',
 'frustration':'they feel {amount} strain around obstruction or unmet expectations',
 'agency':'they feel {amount} able to influence what happens',
 'exploration':'they feel {amount} curiosity about the unfamiliar',
 'reward_pursuit':'anticipated rewards exert {amount} motivational pull',
 'pleasure':'their immediate experience holds {amount} enjoyment',
 'contentment':'they feel {amount} settled satisfaction with the present',
 'affiliation':'they feel {amount} desire for companionship',
 'trust_openness':'they feel {amount} willing to rely on others',
 'social_withdrawal':'they feel {amount} pressure to pull back from social contact',
 'care':'they feel {amount} concern for the welfare of others',
 'separation_distress':'they feel {amount} distress around relational disconnection',
 'grief_sadness':'they feel {amount} sorrow and emotional heaviness',
 'recognition_seeking':'they feel {amount} desire to be acknowledged',
 'shame_exposure_sensitivity':'they feel {amount} sensitive to exposure or negative evaluation',
 'guilt_repair_motivation':'they feel {amount} responsibility around perceived wrongdoing and pressure to make amends',
 'restoration':'they feel {amount} need for rest and recovery',
 'play':'they feel {amount} inclined toward playful engagement',
}
# These are writing relationships, not causal/biological interaction rules.
# Signs stipulate when a useful contrast can be stated without inventing a story.
PAIRS=[
 ('affiliation',1,'social_withdrawal',1),
 ('affiliation',1,'trust_openness',-1),
 ('care',1,'social_withdrawal',1),
 ('self_preservation',1,'mobilization',-1),
 ('self_preservation',1,'alarm',-1),
 ('threat_vigilance',1,'alarm',-1),
 ('alarm',1,'agency',1),
 ('alarm',1,'mobilization',-1),
 ('frustration',1,'agency',-1),
 ('anger_resistance',1,'agency',-1),
 ('mobilization',1,'restoration',1),
 ('reward_pursuit',1,'pleasure',-1),
 ('pleasure',1,'play',-1),
 ('contentment',1,'frustration',1),
 ('recognition_seeking',1,'shame_exposure_sensitivity',1),
]

def synthesize(trace,max_sentences=4,descriptions=None):
    if type(max_sentences) is not int or not 1<=max_sentences<=8:raise ValueError('Sentence budget must be 1..8')
    if descriptions is not None:
        # Absolute-state v2: concatenate actual selected phrases, never reintroduce
        # comparative templates or invent behavioral interaction rules.
        assert len(trace)==len(descriptions)
        ranked=sorted(enumerate(trace),key=lambda pair:(-abs(pair[1]['selected_anchor']),pair[0]))[:max_sentences]
        sentences=[dict(text=descriptions[i]['description'],supports=[t],selection_score=abs(t['selected_anchor'])) for i,t in ranked]
        used={t['state_id'] for _,t in ranked}
        return dict(version='2.0.0',implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),text=' '.join(s['text'] for s in sentences),sentences=sentences,omitted_state_ids=[t['state_id'] for t in trace if t['state_id'] not in used],policy='Exact selected current-state phrases. No temporal comparison or inferred cross-state behavior.')
    states={t['state_id']:t for t in trace}
    def clause(k):
        value=states[k]['selected_anchor']
        degree={2.5:'slightly',5:'moderately',7.5:'markedly',10:'very much'}[abs(value)]
        return CLAUSES[k].format(amount=degree+(' more' if value>0 else ' less'))
    choices=[]
    for order,(a,sa,b,sb) in enumerate(PAIRS):
        if a in states and b in states and states[a]['selected_anchor']*sa>=2.5 and states[b]['selected_anchor']*sb>=2.5:
            score=(abs(states[a]['selected_anchor'])+abs(states[b]['selected_anchor']))/2+.25
            choices.append((score,order,[a,b],f'{clause(a)}, while at the same time {clause(b)}.'))
    for order,t in enumerate(trace):
        if t['selected_anchor']!=0:
            choices.append((abs(t['selected_anchor']),len(PAIRS)+order,[t['state_id']],clause(t['state_id'])+'.'))
    used=set();sentences=[]
    for score,order,keys,text in sorted(choices,key=lambda x:(-x[0],x[1])):
        if used.intersection(keys):continue
        used.update(keys)
        sentences.append(dict(text=text[0].upper()+text[1:],supports=[dict(state_id=k,original_value=states[k]['original_value'],selected_anchor=states[k]['selected_anchor'],option_id=states[k]['option_id']) for k in keys],selection_score=score))
        if len(sentences)>=max_sentences:break
    if not sentences:
        text='The described states remain at their ordinary reference levels; no heightened or reduced state is selected in this draw.'
    else:
        text='Relative to their ordinary baseline: '+' '.join(s['text'] for s in sentences)
    return dict(version=VERSION,implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),text=text,sentences=sentences,omitted_state_ids=[t['state_id'] for t in trace if t['state_id'] not in used],policy='Overview based on selected anchors, not raw-value reinterpretation. All per-state descriptions remain available. Contrast means coexistence, not causation. No recent-change or attention-history inputs.')
