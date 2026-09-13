
import json,re
from pathlib import Path
CUE=re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})")
WORD=re.compile(r"<(\d{2}:\d{2}:\d{2}\.\d{3})>")
TAG=re.compile(r"<[^>]+>")
def sec(x):
 h,m,s=x.split(":"); return int(h)*3600+int(m)*60+float(s)
def parse_vtt(path):
 blocks=re.split(r"\n\s*\n",Path(path).read_text(encoding="utf-8-sig",errors="replace")); out=[]
 for b in blocks:
  m=CUE.search(b)
  if not m: continue
  a,e=sec(m.group(1)),sec(m.group(2)); body=b[m.end():].strip(); ms=list(WORD.finditer(body)); ws=[]
  if ms:
   for i,w in enumerate(ms):
    t=sec(w.group(1)); z=sec(ms[i+1].group(1)) if i+1<len(ms) else e
    q=TAG.sub("",body[w.end():ms[i+1].start() if i+1<len(ms) else len(body)]).strip()
    if q: ws.append({"start":t,"end":z,"text":q})
  else:
   q=re.sub(r"\s+"," ",TAG.sub("",body)).strip()
   if q: ws=[{"start":a,"end":e,"text":q}]
  q=re.sub(r"\s+"," "," ".join(x["text"] for x in ws)).strip()
  if q: out.append({"start":a,"end":e,"text":q,"words":ws})
 clean=[]
 for x in sorted(out,key=lambda x:x["start"]):
  if clean and x["text"].lower()==clean[-1]["text"].lower() and abs(x["start"]-clean[-1]["start"])<.5:
   clean[-1]["end"]=max(clean[-1]["end"],x["end"])
  else: clean.append(x)
 return clean
def write_transcript(vtt,out):
 s=parse_vtt(vtt); p={"language":"en","source":"youtube_auto_captions_vtt","segments":s,"segment_count":len(s),"duration":max([x["end"] for x in s],default=0)}
 Path(out).write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding="utf-8"); return p
