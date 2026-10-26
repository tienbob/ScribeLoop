"""Explicit cloud-backend adapter and evidence-based calibration. No local ML loads."""
from __future__ import annotations

import numpy as np


def vector(value):
    result=np.asarray(value,dtype=np.float32)
    if result.ndim!=1 or not 16<=len(result)<=4096 or not np.isfinite(result).all():
        raise ValueError('Invalid voice embedding')
    norm=np.linalg.norm(result.astype(np.float64))
    if not np.isfinite(norm) or norm<=0:
        raise ValueError('Invalid voice embedding norm')
    return (result.astype(np.float64)/norm).astype(np.float32)


def extract(pcm,model_name='configured-cloud',*,backend=None):
    if backend is None:
        raise ValueError('Configure a cloud speaker-embedding backend; no local fallback is available')
    pcm=np.asarray(pcm,dtype=np.float32)
    if pcm.ndim!=1 or not 30*16000<=pcm.size<=120*16000 or not np.isfinite(pcm).all():
        raise ValueError('Review 30–120 seconds of pure speech before enrollment')
    return vector(backend(pcm,model_name=model_name))


def calibrate(training,heldout,*,model,version,target_far=.01):
    """Labeled cosine pairs, with disjoint people in training and held-out data.

    Input rows: score, same_person, people (two stable IDs), condition. Scores
    must come from the explicitly versioned embedding model, never ASR IDs.
    """
    if not training or not heldout or not 0<target_far<1 or not isinstance(model,str) or not model or not isinstance(version,str) or not version:
        raise ValueError('Calibration requires independent labeled evidence')
    def validate(rows):
        people=set()
        for r in rows:
            if (type(r.get('same_person')) is not bool or not isinstance(r.get('people'),list) or
                    len(r['people'])!=2 or not all(isinstance(p,str) and p for p in r['people']) or
                    not isinstance(r.get('condition'),str) or not r['condition'] or
                    type(r.get('score')) not in (int,float) or not np.isfinite(r['score']) or not -1<=r['score']<=1):
                raise ValueError('Invalid calibration pair')
            people.update(r['people'])
        if not any(r['same_person'] for r in rows) or not any(not r['same_person'] for r in rows):
            raise ValueError('Calibration needs both match and nonmatch pairs')
        return people
    if validate(training)&validate(heldout):
        raise ValueError('Held-out people must be independent')
    def metrics(rows,threshold):
        negatives=[r for r in rows if not r['same_person']]
        positives=[r for r in rows if r['same_person']]
        false=sum(r['score']>=threshold for r in negatives)
        misses=sum(r['score']<threshold for r in positives)
        # Wilson 95% upper bound; sparse evidence must not enable recognition.
        n=len(negatives)
        p=false/n if n else 1
        z=1.96
        upper=(p+z*z/(2*max(n,1))+z*np.sqrt(p*(1-p)/max(n,1)+z*z/(4*max(n,1)**2)))/(1+z*z/max(n,1))
        return {'nonmatches':n,'matches':len(positives),'false_matches':false,'misses':misses,
            'false_match_rate':p,'miss_rate':misses/len(positives) if positives else 1,
            'false_match_upper_95':float(upper)}
    candidates=sorted({float(r['score']) for r in training}|{float(np.nextafter(1.,2.))})
    eligible=[t for t in candidates if metrics(training,t)['false_match_rate']<=target_far]
    threshold=min(eligible,key=lambda t:(metrics(training,t)['miss_rate'],t))
    conditions={condition:metrics([r for r in heldout if r['condition']==condition],threshold)
                for condition in sorted({r['condition'] for r in heldout})}
    complete=set(conditions)=={r['condition'] for r in training}
    ready=complete and all(m['false_match_upper_95']<=target_far and m['matches']>=30 and m['miss_rate']<=.1
                           for m in conditions.values())
    return {'model':model,'version':version,'threshold':threshold,'target_false_match_rate':target_far,
            'heldout':metrics(heldout,threshold),'conditions':conditions,'suggestions_ready':ready,
            'automatic_naming':False}
