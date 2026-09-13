import wave
import numpy as np
from .utils import save_json

TRIGGERS=["oh my god","oh my gosh","no way","what","what the","wait","hold on",
"let's go","lets go","holy","insane","crazy","clutch","how did","did you see",
"i can't believe","i cannot believe","that's crazy","no shot","are you serious",
"what just happened","bro","lmao","hahaha","finally","triple","quad","ace","headshot"]

def audio_events(wav):
    try:
        with wave.open(str(wav),"rb") as w:
            sr=w.getframerate(); ch=w.getnchannels()
            a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)
        if ch>1: a=a.reshape(-1,ch).mean(1)
        step=max(1,int(sr*.5)); vals=[]
        for i in range(0,len(a),step):
            x=a[i:i+step]
            if len(x)>=step//2: vals.append((i/sr,float(np.sqrt(np.mean(x*x))+1e-6)))
        if not vals:return []
        arr=np.array([x[1] for x in vals]); th=np.percentile(arr,92)
        return [{"time":t,"kind":"audio_peak","strength":2} for t,r in vals if r>=th]
    except Exception:return []

def transcript_events(t):
    ev=[]
    for s in t["segments"]:
        low=s["text"].lower(); hits=[x for x in TRIGGERS if x in low]
        if hits: ev.append({"time":s["start"],"kind":"transcript_cue",
                            "strength":min(6,len(hits)),"text":s["text"],"evidence":hits})
        if len(s["text"].split())>=25 and s["end"]-s["start"]>=8:
            ev.append({"time":s["start"],"kind":"dense_speech","strength":1,
                       "text":s["text"],"evidence":[]})
    return ev

def scene_events(video):
    from .utils import run
    r=run(["ffmpeg","-hide_banner","-i",str(video),
           "-vf","select='gt(scene,0.45)',showinfo","-an","-f","null","-"],False)
    out=[]
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try: out.append(float(line.split("pts_time:")[1].split()[0]))
            except: pass
    return [{"time":x,"kind":"scene_change","strength":1,"text":"","evidence":[]} for x in out]

def window(t,a,b):
    return " ".join(s["text"] for s in t["segments"] if s["end"]>=a and s["start"]<=b).strip()

def make_candidates(t,wav,video,cfg,out):
    events=transcript_events(t)+audio_events(wav)+scene_events(video)
    events.sort(key=lambda x:x["time"])
    clusters=[]
    for e in events:
        if not clusters or e["time"]-clusters[-1]["last"]>12:
            clusters.append({"first":e["time"],"last":e["time"],"events":[e]})
        else:
            clusters[-1]["last"]=e["time"]; clusters[-1]["events"].append(e)
    cs=[]; before=cfg["analysis"]["candidate_context_before"]; after=cfg["analysis"]["candidate_context_after"]
    for i,c in enumerate(clusters,1):
        a=max(0,c["first"]-before); b=c["last"]+after
        sig={}; strength=0
        for e in c["events"]:
            sig[e["kind"]]=sig.get(e["kind"],0)+1; strength+=e["strength"]
        cs.append({"candidate_id":i,"event_time":(c["first"]+c["last"])/2,
                   "search_start":a,"search_end":b,"signal_strength":strength,
                   "signals":sig,"evidence":[e["text"] for e in c["events"] if e.get("text")][:8],
                   "transcript_context":window(t,a,b)[:12000]})
    cs.sort(key=lambda x:x["signal_strength"],reverse=True)
    cs=cs[:cfg["analysis"]["max_candidates"]]
    save_json(out,cs); return cs
