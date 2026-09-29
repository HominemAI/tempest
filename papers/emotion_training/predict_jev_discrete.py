"""Load a discrete checkpoint and return 23 signed category IDs."""
import argparse,json,os
from functools import lru_cache
from pathlib import Path
import numpy as np
import torch
from train_jev_discrete import Classifier,SentenceTransformer,file_digest

@lru_cache(maxsize=1)
def load_model(folder):
    config=json.loads((folder/'config.json').read_text())
    encoder_path=Path(os.environ.get('TEMPEST_ENCODER_DIR',config['encoder_path']))
    assert file_digest(encoder_path/'model.safetensors')==config['encoder_sha256']
    torch.set_num_threads(2)
    encoder=SentenceTransformer(str(encoder_path),device='cpu',local_files_only=True)
    # Older checkpoints predate this field and retain their original 384 limit.
    limit=config.get('encoder_limit',384)
    assert limit in (384,512), 'Unsupported encoder context length'
    encoder.max_seq_length=limit
    model=Classifier(config['embedding_dim'])
    model.load_state_dict(torch.load(folder/'weights.pt',map_location='cpu',weights_only=True));model.eval()
    return config,encoder,model

def predict(folder,event,state):
    assert isinstance(event,str) and event.strip(), 'Event must be nonempty'
    assert isinstance(state,list) and len(state)==23 and all(type(v) in (int,float) and np.isfinite(v) and -10<=v<=10 for v in state), 'Expected 23 finite values in [-10,10]'
    config,encoder,model=load_model(folder)
    assert len(encoder.tokenizer(event,truncation=False)['input_ids'])<=encoder.max_seq_length, 'Event exceeds encoder context; no silent truncation'
    with torch.inference_mode():
        embedding=encoder.encode([event],normalize_embeddings=True,convert_to_numpy=True)
        result=(model(torch.from_numpy(embedding),torch.tensor([state],dtype=torch.float32)).argmax(-1)-4)[0].tolist()
    return dict(categories=result,by_state=dict(zip(config['dimensions'],result)),meaning='Ordered change categories, not numeric state deltas.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',type=Path,required=True);p.add_argument('--event',required=True);p.add_argument('--state',default='[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]');a=p.parse_args()
    print(json.dumps(predict(a.model,a.event,json.loads(a.state)),indent=2))
