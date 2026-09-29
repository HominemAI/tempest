"""Versioned, local state-vector to descriptor mapping. No model/API required."""
import argparse, hashlib, json, math, random
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'papers/personality-mapping/v1/anchors.json'
def active_mapping(kind='state'):
    """Resolve only fully published, validated index pairs; v1 remains available."""
    if kind not in ('state','delta'):raise ValueError('Unknown index kind')
    pointer=ROOT/'papers/personality-mapping/active.json'
    if not pointer.exists():
        if kind=='state':return DEFAULT
        raise FileNotFoundError('Delta index is awaiting generation and validation')
    data=json.loads(pointer.read_text())
    path=ROOT/data[kind]
    if not path.resolve().is_relative_to((ROOT/'papers/personality-mapping').resolve()):raise ValueError('Invalid index path')
    return path
VALUES=[-10,-7.5,-5,-2.5,0,2.5,5,7.5,10]
TERMS={
 'self_preservation':('motivation to safeguard their own safety and integrity','A low protective drive does not establish an intention to be harmed or a decision to take risks.'),
 'threat_vigilance':('attention devoted to monitoring possible danger','Low monitoring does not establish that the surroundings are safe; high monitoring is not proof of danger.'),
 'mobilization':('activation and readiness to act','Low readiness does not establish physical incapacity; high readiness does not specify an action.'),
 'alarm':('immediate apprehension and felt urgency','Low alarm does not establish safety, and high alarm does not establish panic or a specific threat.'),
 'aversion':('repulsion and pressure to reject or avoid an unwanted stimulus','Low aversion does not imply attraction, approval, consent, or social affiliation.'),
 'anger_resistance':('internal pressure to oppose or push back','Low opposition does not imply agreement or submission; high opposition does not establish aggression.'),
 'frustration':('strain associated with obstruction or unmet expectations','This state does not identify an obstacle or explain its cause; do not invent one.'),
 'agency':('felt ability to influence what happens','Perceived influence is not actual power, competence, physical energy, or guaranteed success.'),
 'exploration':('curiosity and interest in investigating the unfamiliar','Low curiosity does not imply low intelligence or aversion; high curiosity is not necessarily threat monitoring.'),
 'reward_pursuit':('motivational pull toward anticipated rewarding outcomes','Wanting a reward is distinct from enjoying it, and does not specify which reward or action.'),
 'pleasure':('immediate enjoyment and pleasant feeling','Low enjoyment does not by itself establish pain, sadness, or a diagnosis.'),
 'contentment':('settled satisfaction and ease with the present situation','Low contentment does not specify anger or a blocked goal; high contentment need not be exciting.'),
 'affiliation':('desire for connection and companionship','Low desire for company does not establish rejection or distrust; desire for company can coexist with withdrawal.'),
 'trust_openness':('willingness to rely on others and be interpersonally receptive','Low receptivity does not establish hostility; high receptivity does not establish that others are trustworthy.'),
 'social_withdrawal':('pressure to reduce or retreat from social contact','Low withdrawal does not necessarily create a desire for company; high withdrawal can coexist with wanting connection.'),
 'care':('other-directed concern and nurturing motivation','Low caring motivation does not imply cruelty, and high caring motivation does not mean care has been given.'),
 'separation_distress':('distress associated with relational separation or disconnection','The baseline does not identify a separation event or prove that anyone has rejected this person.'),
 'grief_sadness':('felt sorrow and emotional heaviness','The baseline does not imply a bereavement, a diagnosis, or a specific cause; low sorrow is not automatically joy.'),
 'recognition_seeking':('desire to be noticed and acknowledged','Low recognition seeking is not low self-worth; high recognition seeking does not imply entitlement.'),
 'shame_exposure_sensitivity':('sensitivity to being exposed or negatively evaluated','Sensitivity does not establish that anyone is observing or judging, or that wrongdoing occurred.'),
 'guilt_repair_motivation':('felt responsibility for perceived wrongdoing and pressure to make amends','This does not prove wrongdoing or identify its cause; low repair pressure does not imply lack of ethics.'),
 'restoration':('felt need for rest and recovery','This is restorative pressure, not measured exhaustion; low restorative pressure does not guarantee ample energy.'),
 'play':('inclination toward playful and lighthearted engagement','Low playfulness does not imply lack of enjoyment, and high playfulness does not guarantee joking or outward play.')
}

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def phrases(term,value):
    if value==0:
        return [f'Their {term} is at its ordinary reference level, with no stipulated elevation or suppression.',
                f'For {term}, they are at an ordinary baseline rather than a heightened or reduced state.',
                f'Their current baseline for {term} is ordinary; this does not mean the capacity is absent.']
    degree={2.5:'slightly',5:'moderately',7.5:'markedly',10:'very strongly'}[abs(value)]
    direction='above' if value>0 else 'below';adjective='heightened' if value>0 else 'reduced'
    return [f'Their {term} is {degree} {direction} its ordinary reference level.',
            f'Compared with an ordinary baseline, their {term} is {degree} {adjective}.',
            f'Their current level of {term} is {degree} {direction} the reference baseline.']

def candidates(value,radius=2.5):
    if not math.isfinite(radius) or radius<=0:raise ValueError('Radius must be finite and positive')
    if value==0:return [(0,1.)]
    pool=[(a,max(0.,1-abs(a-value)/radius)) for a in VALUES if a*value>=0]
    pool=[(a,w) for a,w in pool if w>0]
    if not pool:pool=[(min([a for a in VALUES if a*value>=0],key=lambda a:abs(a-value)),1.)]
    total=sum(w for _,w in pool)
    return [(a,w/total) for a,w in pool]

def build(out):
    source=ROOT/'papers/jev-nine-level-request-v4.json'
    rubric=json.loads(source.read_text())['state']['rubric']
    assert {r['id'] for r in rubric}==set(TERMS)
    data=dict(version='1.0.0',status='draft simulation convention; not a validated psychological scale',range=[-10,10],neutral=0,anchor_values=VALUES,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),dimensions=[])
    for r in rubric:
        term,guard=TERMS[r['id']]
        anchors=[dict(value=v,options=[dict(id=f"{r['id']}:{v}:{i}",text=t) for i,t in enumerate(phrases(term,v),1)]) for v in VALUES]
        data['dimensions'].append(dict(**r,baseline_guard=guard,anchors=anchors))
    if out.exists():raise FileExistsError('Use a new version directory; do not overwrite an existing mapping')
    out.mkdir(parents=True)
    write(out/'anchors.json',data)
    write(out/'tenths-index.json',dict(version='1.0.0',radius=2.5,dimensions={r['id']:[dict(value=i/10,candidates=[dict(anchor=a,weight=w) for a,w in candidates(i/10)]) for i in range(-100,101)] for r in rubric}))
    return data

def validate_vector(vector):
    if not isinstance(vector,list) or len(vector)!=23:raise ValueError('Expected an ordered list of 23 values')
    if any(type(v) not in (float,int) or not math.isfinite(v) or not -10<=v<=10 for v in vector):raise ValueError('Values must be finite numbers in [-10,10], not booleans; values are never silently clamped')

def describe(vector,mapping,seed=42,radius=2.5,mode='sample',richness=True):
    validate_vector(vector)
    if mode not in ('sample','nearest'):raise ValueError('Mode must be sample or nearest')
    data=json.loads(mapping.read_text());assert data['version'] in ('1.0.0','2.0.0') and len(data['dimensions'])==23
    traces=[];teacher=[]
    for original,state in zip(vector,data['dimensions']):
        value=float(Decimal(str(original)).quantize(Decimal('.1'),rounding=ROUND_HALF_UP))
        pool=candidates(value,radius)
        # Per-state RNG avoids changes elsewhere perturbing all selected wording.
        seed_material=f"{seed}|{data['version']}|{state['id']}|{value}"
        rng=random.Random(int(hashlib.sha256(seed_material.encode()).hexdigest(),16))
        selected=rng.choices([a for a,w in pool],weights=[w for a,w in pool])[0] if mode=='sample' else min(pool,key=lambda p:(abs(p[0]-value),abs(p[0])))[0]
        anchor=next(a for a in state['anchors'] if a['value']==selected)
        option=rng.choice(anchor['options']) if mode=='sample' else anchor['options'][0]
        teacher.append(dict(state=state['name'],description=option['text']))
        traces.append(dict(state_id=state['id'],original_value=original,quantized_value=value,selected_anchor=selected,option_id=option['id'],candidates=[dict(anchor=a,weight=w) for a,w in pool]))
    result=dict(mapping_version=data['version'],mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),seed=seed,mode=mode,radius=radius,starting_state=vector,trace=traces,
      teacher_payload=dict(baseline_policy='These descriptions specify the current internal baseline before the event, not changes, causes, diagnoses, decisions, or certain actions. Each state is independent unless the event provides a connection. Reduced does not imply the opposite trait. Ordinary does not mean absent.',states=teacher))
    if richness:
        from personality_synthesis import synthesize
        result['synthesis']=synthesize(traces,descriptions=teacher if data['version']=='2.0.0' else None)
        result['teacher_payload']['overview']=result['synthesis']['text']
        result['teacher_payload']['overview_policy']='This overview summarizes selected states; omitted states are still specified below. Juxtaposition does not imply a causal relationship.'
    if data['version']=='2.0.0':
        result['teacher_payload'].pop('baseline_policy',None)
        result['teacher_payload']['state_policy']='These descriptions express the individual\'s current internal states, not changes, causes, diagnoses, completed actions, or personal historical norms. Low intensity does not establish an opposite trait. Conditional inclinations do not guarantee behavior.'
    return result

def render(data):
    lines=['# Personality state anchors v1','',data['status'],'','Current-state values: −10…+10. These are NOT the −4…+4 change categories.','']
    for state in data['dimensions']:
        lines.extend([f"## {state['name']}",'',state['definition'],'',state['distinctions'],'',state['baseline_guard'],'','| State value | Canonical descriptor |','|---:|---|'])
        lines.extend(f"| {a['value']} | {a['options'][0]['text']} |" for a in state['anchors']);lines.append('')
    return '\n'.join(lines)+'\n'

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--out',type=Path,default=DEFAULT.parent)
    d=sub.add_parser('describe');d.add_argument('--vector',required=True);d.add_argument('--mapping',type=Path,default=DEFAULT);d.add_argument('--seed',type=int,default=42);d.add_argument('--radius',type=float,default=2.5);d.add_argument('--mode',choices=['sample','nearest'],default='sample');d.add_argument('--teacher-only',action='store_true')
    d.add_argument('--plain',action='store_true',help='Omit the grounded synthesis; retain the original per-state-only payload')
    a=p.parse_args()
    if a.command=='build':
        data=build(a.out);(a.out/'ANCHORS.md').write_text(render(data));print(f'Built 207 anchors, 621 phrasing options, and 4,623 tenth-value entries in {a.out}')
    else:
        result=describe(json.loads(a.vector),a.mapping,a.seed,a.radius,a.mode,richness=not a.plain)
        print(json.dumps(result['teacher_payload'] if a.teacher_only else result,ensure_ascii=False,indent=2))
