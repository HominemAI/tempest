"""Offline Tempest discrete classifier. Never changes teacher records or calls APIs."""
import argparse, hashlib, json, os, random, re, sqlite3, time, traceback
from datetime import datetime, timezone
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import numpy as np
import torch
from torch import nn
from sklearn.model_selection import GroupShuffleSplit
from sentence_transformers import SentenceTransformer
from pipeline import versions, file_digest

ROOT=Path(__file__).resolve().parents[2]
B=ROOT/'papers/emotion-labeling/batches'
ENCODER=ROOT/'papers/emotion_training/artifacts/capacity-comparison-20260926-v1/mpnet-encoder'
RUNS=['jev-all-neutral-v4-nine-20260927','jev-personality-100x100-v4-20260927']

def save(path,data):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)

def status(out,phase,**kw):
    data=dict(pid=os.getpid(),updated=datetime.now(timezone.utc).isoformat(),phase=phase,**kw)
    save(out/'status.json',data);print(json.dumps(data),flush=True)

def target(response,dimensions):
    assert set(response['answers'])==set(dimensions)
    labels=[]
    for dim in dimensions:
        p=response['answers'][dim]['probabilities']
        assert set(p)==set(map(str,range(9)))
        a=np.array([p[str(i)] for i in range(9)],dtype=float)
        assert np.isfinite(a).all() and (a>=0).all() and (a<=1).all() and abs(a.sum()-1)<=.051
        winners=np.flatnonzero(np.isclose(a,a.max(),rtol=0,atol=1e-9))
        labels.append(int(winners[0]) if len(winners)==1 else -100)
    return labels

def load():
    rows=[];sources={};dimensions=None
    for name in RUNS:
        run=B/name;m=json.loads((run/'manifest.json').read_text())
        t=json.loads((run/'request-template.json').read_text());dims=list(t['questions'])
        if dimensions is None:dimensions=dims
        assert dimensions==dims and len(dims)==23
        assert file_digest(run/'request-template.json')==m['template_sha256']
        assert json.loads((run/'status.json').read_text())['counts']=={'complete':m['records']}
        db=sqlite3.connect('file:'+str(run/'results.sqlite')+'?mode=ro',uri=True)
        conditions={}
        if 'personality' in name:
            conditions={k:(p,e,json.loads(s)) for k,p,e,s in db.execute('SELECT * FROM conditioning')}
        for ident,event,group,response in db.execute('SELECT id,scenario,split_group,response FROM events ORDER BY ordinal'):
            profile,eid,state=conditions.get(ident,('neutral',ident,[0.]*23))
            assert len(state)==23 and all(type(v) in (float,int) and np.isfinite(v) and -10<=v<=10 for v in state)
            rows.append(dict(id=name+'/'+ident,event_id=eid,text=event,group=group or eid,profile=profile,state=state,target=target(json.loads(response),dims)))
        db.close();sources[name]=dict(database_sha256=file_digest(run/'results.sqlite'),template_sha256=m['template_sha256'])
    assert len(rows)==52600
    # Union exact normalized duplicates and shared source families, including all baselines.
    parent={};norms={}
    def find(x):
        parent.setdefault(x,x)
        while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
        return x
    for r in rows:
        key=re.sub(r'\W+',' ',r['text'].casefold()).strip()
        if key in norms:parent[find(r['group'])]=find(norms[key])
        norms[key]=r['group']
    for r in rows:r['group']=find(r['group'])
    return rows,dimensions,sources

def partitions(rows,seed):
    groups=np.array([r['group'] for r in rows]);ix=np.arange(len(rows))
    tv,te=next(GroupShuffleSplit(n_splits=1,test_size=.15,random_state=seed).split(ix,groups=groups))
    tr,va=next(GroupShuffleSplit(n_splits=1,test_size=.15/.85,random_state=seed+1).split(tv,groups=groups[tv]))
    tr,va=tv[tr],tv[va]
    for a,b in ((tr,va),(tr,te),(va,te)):assert not set(groups[a])&set(groups[b])
    profiles=sorted({r['profile'] for r in rows}-{'neutral'});random.Random(seed).shuffle(profiles)
    train_profiles=set(profiles[:80])|{'neutral'};val_profiles=set(profiles[80:90]);test_profiles=set(profiles[90:])
    result={'train':np.array([i for i in tr if rows[i]['profile'] in train_profiles],dtype=int),
            'val':np.array([i for i in va if rows[i]['profile'] in train_profiles|val_profiles],dtype=int),
            'test':np.array([i for i in te if rows[i]['profile'] in train_profiles|test_profiles],dtype=int)}
    return result,dict(train=sorted(train_profiles),val=sorted(val_profiles),test=sorted(test_profiles))

class Classifier(nn.Module):
    def __init__(self,embedding_dim):
        super().__init__();self.net=nn.Sequential(nn.Linear(embedding_dim+23,256),nn.ReLU(),nn.Dropout(.1),nn.Linear(256,128),nn.ReLU(),nn.Linear(128,23*9))
    def forward(self,event,state):return self.net(torch.cat([event,state/10.],dim=-1)).reshape(-1,23,9)

def loss_fn(logits,y):
    # Hard category targets only; ordinal term penalizes distant mistakes.
    mask=y!=-100
    ce=nn.functional.cross_entropy(logits.reshape(-1,9),y.reshape(-1),ignore_index=-100)
    distance=(torch.arange(9,device=y.device)[None,None,:]-y.clamp_min(0)[:,:,None]).abs()/8
    ordinal=(logits.softmax(-1)*distance).sum(-1)[mask].mean()
    return ce+.25*ordinal

def metrics(y,p):
    mask=y!=-100
    if not mask.any():return {'labeled_cells':0}
    a,b=y[mask],p[mask];zero=a==4
    return dict(labeled_cells=int(mask.sum()),exact_accuracy=float((a==b).mean()),within_one_accuracy=float((abs(a-b)<=1).mean()),category_mae=float(abs(a-b).mean()),direction_accuracy=float((np.sign(a-4)==np.sign(b-4)).mean()),false_activation_on_zero=float((b[zero]!=4).mean()) if zero.any() else None)

def run(args):
    out=args.out;torch.set_num_threads(2);torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    out.mkdir(parents=True,exist_ok=False);status(out,'preparing')
    rows,dims,sources=load();parts,profiles=partitions(rows,args.seed)
    encoder=SentenceTransformer(str(ENCODER),device='cpu',local_files_only=True);encoder.eval()
    # Local override only: preserve the shared encoder and previous checkpoints.
    encoder.max_seq_length=args.max_seq_length
    unique=list(dict.fromkeys(r['text'] for r in rows));positions={t:i for i,t in enumerate(unique)}
    lengths=[len(encoder.tokenizer(t,truncation=False)['input_ids']) for t in unique]
    long={t for t,n in zip(unique,lengths) if n>encoder.max_seq_length}
    # Explicitly exclude overlength examples rather than silently truncate.
    for k in parts:parts[k]=np.array([i for i in parts[k] if rows[i]['text'] not in long and any(v!=-100 for v in rows[i]['target'])],dtype=int)
    texts=[t for t in unique if t not in long];positions={t:i for i,t in enumerate(texts)}
    metadata=dict(dimensions=dims,sources=sources,seed=args.seed,packages=versions(),profiles=profiles,counts={k:len(v) for k,v in parts.items()},records=len(rows),ties=sum(v==-100 for r in rows for v in r['target']),excluded_overlength=[dict(id=r['id'],tokens=lengths[unique.index(r['text'])]) for r in rows if r['text'] in long],input='raw event text + 23 float states divided by 10; no descriptions, perspective, or teacher explanations',output='23 discrete integers -4..4; internal 9-way logits per state',loss='hard cross entropy + 0.25 expected ordinal distance / 8; tied labels masked',encoder_path=str(ENCODER),encoder_sha256=file_digest(ENCODER/'model.safetensors'),encoder_limit=encoder.max_seq_length,caveats=['Synthetic teacher agreement, not human accuracy.','Teacher descriptors are generated and unvalidated.','Profile/event split intersections omitted to preserve independent holdouts.','Exact duplicate and source-family grouping does not catch all semantic paraphrases.'])
    save(out/'metadata.json',metadata)
    with (out/'records.jsonl').open('w') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    status(out,'encoding',unique_events=len(texts),counts=metadata['counts'],tied_cells=metadata['ties'],excluded_long=len(metadata['excluded_overlength']))
    encoded=[]
    for start in range(0,len(texts),256):
        encoded.append(encoder.encode(texts[start:start+256],batch_size=16,normalize_embeddings=True,convert_to_numpy=True,show_progress_bar=False))
        status(out,'encoding',encoded=min(start+256,len(texts)),total=len(texts))
    embeddings=np.concatenate(encoded).astype(np.float32)
    e=np.array([embeddings[positions[r['text']]] if r['text'] in positions else np.zeros(embeddings.shape[1]) for r in rows],dtype=np.float32)
    s=np.array([r['state'] for r in rows],dtype=np.float32);y=np.array([r['target'] for r in rows],dtype=np.int64)
    assert np.isfinite(e).all() and np.isfinite(s).all()
    np.savez_compressed(out/'features.npz',embeddings=e,states=s,targets=y,**parts)
    metadata['features_sha256']=file_digest(out/'features.npz');metadata['records_sha256']=file_digest(out/'records.jsonl');save(out/'metadata.json',metadata)
    model=Classifier(e.shape[1]);optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
    et,st,yt=torch.from_numpy(e),torch.from_numpy(s),torch.from_numpy(y)
    def evaluate(ix,zero_state=False):
        pred=[];total_loss=0;cells=0
        with torch.inference_mode():
            for start in range(0,len(ix),512):
                j=ix[start:start+512];logits=model(et[j],torch.zeros_like(st[j]) if zero_state else st[j]);pred.append(logits.argmax(-1).numpy());n=int((yt[j]!=-100).sum());total_loss+=float(loss_fn(logits,yt[j]))*n;cells+=n
        return np.concatenate(pred),total_loss/max(cells,1)
    best=float('inf');stale=0;history=[]
    for epoch in range(1,args.epochs+1):
        model.train();order=np.random.permutation(parts['train'])
        for start in range(0,len(order),128):
            j=order[start:start+128];optimizer.zero_grad();loss=loss_fn(model(et[j],st[j]),yt[j]);loss.backward();optimizer.step()
        model.eval();pred,val=evaluate(parts['val']);history.append(dict(epoch=epoch,val_loss=val,metrics=metrics(y[parts['val']],pred)))
        if val<best:best=val;stale=0;torch.save(model.state_dict(),out/'weights.pt')
        else:stale+=1
        save(out/'history.json',history);status(out,'training',epoch=epoch,validation_loss=val,best_loss=best,patience_used=stale)
        if stale>=15:break
    model.load_state_dict(torch.load(out/'weights.pt',map_location='cpu',weights_only=True));model.eval();test=parts['test'];pred,_=evaluate(test)
    majority=np.array([np.bincount(y[parts['train'],d][y[parts['train'],d]>=0],minlength=9).argmax() for d in range(23)])
    report=dict(test=metrics(y[test],pred),zero_baseline=metrics(y[test],np.full_like(pred,4)),train_mode_baseline=metrics(y[test],np.broadcast_to(majority,pred.shape)),trainable_parameters=sum(p.numel() for p in model.parameters()),encoder_parameters=sum(p.numel() for p in encoder.parameters()),best_epoch=min(history,key=lambda r:r['val_loss'])['epoch'],groups={})
    for name,mask in [('neutral',np.array([rows[i]['profile']=='neutral' for i in test])),('seen_personalities',np.array([rows[i]['profile'] in profiles['train'] and rows[i]['profile']!='neutral' for i in test])),('unseen_personalities',np.array([rows[i]['profile'] in profiles['test'] for i in test]))]:
        report['groups'][name]=dict(records=int(mask.sum()),**metrics(y[test][mask],pred[mask]))
    report['per_state']={dim:metrics(y[test,d],pred[:,d]) for d,dim in enumerate(dims)}
    no_state,_=evaluate(test,True);report['zeroed_input_ablation']=metrics(y[test],no_state)
    np.savez_compressed(out/'test-predictions.npz',indices=test,actual=y[test]-4,predicted=pred-4,valid_mask=y[test]!=-100)
    config=dict(embedding_dim=e.shape[1],dimensions=dims,state_scale=10,encoder_path=str(ENCODER),encoder_sha256=metadata['encoder_sha256'],encoder_limit=encoder.max_seq_length,output='argmax(logits)-4',architecture=[e.shape[1]+23,256,128,207])
    save(out/'config.json',config)
    times=[]
    for i in test[:55]:
        start=time.perf_counter()
        with torch.inference_mode():
            embed=encoder.encode([rows[i]['text']],normalize_embeddings=True,convert_to_numpy=True)
            result=model(torch.from_numpy(embed),st[i:i+1]).argmax(-1)-4
        times.append((time.perf_counter()-start)*1000)
        assert result.shape==(1,23) and ((result>=-4)&(result<=4)).all()
    report['warm_cpu_latency_ms']=dict(median=float(np.median(times[5:])),p95=float(np.percentile(times[5:],95)),samples=len(times[5:]),threads=2)
    save(out/'metrics.json',report);status(out,'complete',test=report['test'],groups=report['groups'],trainable_parameters=report['trainable_parameters'],latency_ms=report['warm_cpu_latency_ms'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--seed',type=int,default=42);p.add_argument('--epochs',type=int,default=120);p.add_argument('--max-seq-length',type=int,choices=[384,512],default=384);a=p.parse_args()
    try:run(a)
    except Exception:
        if a.out.exists():status(a.out,'failed',error=traceback.format_exc())
        raise
