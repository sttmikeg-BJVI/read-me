from __future__ import annotations
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from .analyzer import analyze_beat, analyze_performance
from .organizer import (
    PRODUCTION_TEMPLATE,
    assign_best,
    grid_payload,
    normalize_lane,
    normalize_status,
    suggest_matches,
)
from .store import ensure_dirs, load_beats, load_songs, save_beats, save_songs, UPLOADS

app = FastAPI(title="AI Music Production Studio")
ensure_dirs()

INDEX = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Music Production Studio</title>
<style>
body{font-family:Arial,sans-serif;margin:0;background:#111;color:#eee}
main{max-width:1400px;margin:auto;padding:20px}
h1{margin:0 0 6px}.muted{color:#aaa}
.panel{background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:16px;margin:14px 0}
.forms{display:grid;grid-template-columns:1fr 1fr;gap:14px}
input,textarea,select,button{width:100%;box-sizing:border-box;padding:9px;margin:5px 0;border-radius:7px;border:1px solid #444;background:#222;color:#eee}
button{cursor:pointer;background:#333}.small{font-size:12px}.gridwrap{overflow-x:auto}
table{border-collapse:separate;border-spacing:8px;min-width:1150px;width:100%}
th{font-size:12px;color:#bbb;text-transform:uppercase}td{vertical-align:top;background:#171717;border:1px solid #333;border-radius:9px;padding:8px;min-width:190px}
.lane{font-weight:bold;width:160px}.card{border:1px solid #444;background:#242424;border-radius:8px;padding:9px;margin:6px 0}
.card strong{display:block}.meta{font-size:11px;color:#aaa;margin:4px 0}.actions{display:flex;gap:6px;flex-wrap:wrap}.actions button{width:auto;font-size:11px;padding:6px 8px}
audio{width:100%;height:34px}.matches{font-size:11px;margin-top:6px}.ok{color:#8ed081}.err{color:#ff8b8b}
@media(max-width:800px){.forms{grid-template-columns:1fr}}
</style>
</head>
<body><main>
<h1>AI Music Production Studio</h1>
<div class="muted">Upload beats + songs, analyze them, match them, and place them on the production grid.</div>

<div class="forms">
<section class="panel">
<h2>Add Beat</h2>
<form id="beatForm">
<input name="title_prefix" placeholder="Optional title prefix">
<input name="tags" placeholder="Tags: cinematic, street, romantic">
<input type="file" name="files" accept="audio/*" multiple required>
<button>Upload + Analyze Beat(s)</button>
</form>
<div id="beatMsg" class="small"></div>
</section>

<section class="panel">
<h2>Add Song / Performance</h2>
<form id="songForm">
<input name="title" placeholder="Song title" required>
<select name="lane">
<option value="unassigned">Unassigned</option>
<option value="club-women">Club — Women</option>
<option value="romantic-women">Romantic — Women</option>
<option value="club-open">Club — Men / Both (Jack)</option>
<option value="street">Street</option>
<option value="cinematic">Cinematic</option>
</select>
<input name="tags" placeholder="Tags / mood / energy">
<textarea name="lyrics" placeholder="Paste lyrics or song notes"></textarea>
<textarea name="notes" placeholder="Production notes"></textarea>
<input type="file" name="audio" accept="audio/*">
<button>Save Song + Analyze Audio</button>
</form>
<div id="songMsg" class="small"></div>
</section>
</div>

<section class="panel">
<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
<h2 style="margin-right:auto">Production Grid</h2>
<input id="filter" style="max-width:280px" placeholder="Filter title, lane, status">
<button id="refresh" style="width:auto">Refresh</button>
</div>
<div id="summary" class="small muted"></div>
<div id="grid" class="gridwrap"></div>
</section>

<script>
const q=s=>document.querySelector(s);
async function postForm(url, form){
  const r=await fetch(url,{method:"POST",body:new FormData(form)});
  const data=await r.json();
  if(!r.ok) throw new Error(data.detail||"Request failed");
  return data;
}
q("#beatForm").addEventListener("submit",async e=>{
  e.preventDefault(); q("#beatMsg").textContent="Analyzing...";
  try{const out=await postForm("/beats/bulk",e.target);q("#beatMsg").textContent=out.added+" beat(s) added.";e.target.reset();await load();}
  catch(err){q("#beatMsg").textContent=err.message;}
});
q("#songForm").addEventListener("submit",async e=>{
  e.preventDefault(); q("#songMsg").textContent="Saving...";
  try{await postForm("/songs",e.target);q("#songMsg").textContent="Song added.";e.target.reset();await load();}
  catch(err){q("#songMsg").textContent=err.message;}
});
q("#refresh").addEventListener("click",load);
q("#filter").addEventListener("input",load);

async function action(url){
  const r=await fetch(url,{method:"POST"});
  const data=await r.json();
  if(!r.ok){alert(data.detail||"Action failed");return;}
  await load();
}
async function showMatches(id){
  const r=await fetch("/songs/"+id+"/match",{method:"POST"});
  const data=await r.json();
  if(!r.ok){alert(data.detail||"Match failed");return;}
  const box=document.getElementById("m_"+id);
  box.innerHTML=(data.matches||[]).slice(0,5).map(x=>"<div>"+x.score+" — "+esc(x.title)+" <button onclick=\"action('/songs/"+id+"/assign/"+x.beat_id+"')\" style='width:auto;padding:3px 6px'>Assign</button></div>").join("")||"No beats yet";
}
function esc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]));}
function card(s){
  const audio=s.audio_path?'<audio controls src="/songs/'+s.id+'/audio"></audio>':"";
  const beat=s.assigned_beat_title?'<div class="meta">Beat: '+esc(s.assigned_beat_title)+' · '+(s.match_score??"")+"%</div>":"";
  return '<div class="card"><strong>'+esc(s.title)+'</strong>'+
    '<div class="meta">'+esc(s.lane)+' · '+esc(s.status)+(s.estimated_bpm?(" · "+s.estimated_bpm.toFixed(1)+" BPM"):"")+'</div>'+
    beat+audio+
    '<div class="actions"><button onclick="showMatches(\''+s.id+'\')">Match</button><button onclick="action(\'/songs/'+s.id+'/auto-allocate\')">Auto allocate</button><button onclick="action(\'/songs/'+s.id+'/ready\')">Mark ready</button></div>'+
    '<div class="matches" id="m_'+s.id+'"></div></div>';
}
async function load(){
  const r=await fetch("/api/state"); const data=await r.json();
  const f=q("#filter").value.trim().toLowerCase();
  const songs=(data.songs||[]).filter(s=>!f||[s.title,s.lane,s.status,s.assigned_beat_title].join(" ").toLowerCase().includes(f));
  q("#summary").textContent=songs.length+" songs · "+(data.beats||[]).length+" beats";
  const columns=data.template.columns;
  const lanes=data.template.lanes;
  let html="<table><thead><tr><th>Lane</th>"+columns.map(c=>"<th>"+c+"</th>").join("")+"</tr></thead><tbody>";
  for(const lane of lanes){
    html+="<tr><td class='lane'>"+esc(lane.label)+"</td>";
    for(const status of columns){
      const cell=songs.filter(s=>s.lane===lane.id&&s.status===status);
      html+="<td>"+cell.map(card).join("")+"</td>";
    }
    html+="</tr>";
  }
  html+="</tbody></table>"; q("#grid").innerHTML=html;
}
load();
</script>
</main></body></html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return INDEX

def _audio_ext(filename: str | None):
    ext = Path(filename or "").suffix.lower()
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        raise HTTPException(400, "Unsupported audio type")
    return ext

@app.post("/beats")
async def add_beat(
    title: str = Form(...),
    tags: str = Form(""),
    file: UploadFile = File(...),
):
    ext = _audio_ext(file.filename)
    beat_id = uuid.uuid4().hex[:12]
    target = UPLOADS / f"beat_{beat_id}{ext}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    beat = analyze_beat(str(target), beat_id, title, [x.strip() for x in tags.split(",") if x.strip()])
    beats = load_beats()
    beats.append(beat)
    save_beats(beats)
    return {"beat": beat}

@app.post("/beats/bulk")
async def add_beats_bulk(
    title_prefix: str = Form(""),
    tags: str = Form(""),
    files: list[UploadFile] = File(...),
):
    beats = load_beats()
    added = []
    tag_list = [x.strip() for x in tags.split(",") if x.strip()]
    for file in files:
        ext = _audio_ext(file.filename)
        beat_id = uuid.uuid4().hex[:12]
        source_title = Path(file.filename or beat_id).stem
        title = f"{title_prefix.strip()} {source_title}".strip() if title_prefix.strip() else source_title
        target = UPLOADS / f"beat_{beat_id}{ext}"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        beat = analyze_beat(str(target), beat_id, title, tag_list)
        beats.append(beat)
        added.append(beat)
    save_beats(beats)
    return {"added": len(added), "beats": added}

@app.get("/beats")
def beats():
    return {"beats": load_beats()}

@app.get("/beats/{beat_id}/audio")
def beat_audio(beat_id: str):
    beat = next((b for b in load_beats() if b.id == beat_id), None)
    if not beat:
        raise HTTPException(404, "Beat not found")
    return FileResponse(beat.path)

@app.post("/songs")
async def add_song(
    title: str = Form(...),
    lane: str = Form("unassigned"),
    tags: str = Form(""),
    lyrics: str = Form(""),
    notes: str = Form(""),
    audio: UploadFile | None = File(None),
):
    from .models import SongRecord
    song_id = uuid.uuid4().hex[:12]
    song = SongRecord(
        id=song_id,
        title=title.strip(),
        lyrics=lyrics,
        lane=normalize_lane(lane),
        tags=[x.strip() for x in tags.split(",") if x.strip()],
        notes=notes,
    )
    if audio and audio.filename:
        ext = _audio_ext(audio.filename)
        target = UPLOADS / f"song_{song_id}{ext}"
        with target.open("wb") as out:
            shutil.copyfileobj(audio.file, out)
        perf = analyze_performance(str(target))
        song.audio_path = str(target)
        song.duration = perf.duration
        song.estimated_bpm = perf.estimated_bpm
        song.energy = perf.energy
        song.onset_density = perf.onset_density
        song.pause_ratio = perf.pause_ratio
        song.status = "ANALYZED"
    songs = load_songs()
    songs.append(song)
    save_songs(songs)
    return {"song": song}

@app.get("/songs")
def songs():
    return {"songs": load_songs()}

@app.get("/songs/{song_id}/audio")
def song_audio(song_id: str):
    song = next((s for s in load_songs() if s.id == song_id), None)
    if not song or not song.audio_path:
        raise HTTPException(404, "Song audio not found")
    return FileResponse(song.audio_path)

@app.post("/songs/{song_id}/match")
def match_song(song_id: str):
    songs = load_songs()
    song = next((s for s in songs if s.id == song_id), None)
    if not song:
        raise HTTPException(404, "Song not found")
    matches = suggest_matches(song, load_beats())
    if matches and song.status not in {"ASSIGNED", "READY"}:
        song.status = "MATCHED"
        save_songs(songs)
    return {"song_id": song_id, "matches": matches[:10]}

@app.post("/songs/{song_id}/auto-allocate")
def auto_allocate(song_id: str):
    songs = load_songs()
    song = next((s for s in songs if s.id == song_id), None)
    if not song:
        raise HTTPException(404, "Song not found")
    best = assign_best(song, load_beats())
    if not best:
        raise HTTPException(409, "No beats are available to allocate")
    save_songs(songs)
    return {"song": song, "assigned": best}

@app.post("/songs/{song_id}/assign/{beat_id}")
def assign_specific(song_id: str, beat_id: str):
    songs = load_songs()
    song = next((s for s in songs if s.id == song_id), None)
    if not song:
        raise HTTPException(404, "Song not found")
    beats = load_beats()
    beat = next((b for b in beats if b.id == beat_id), None)
    if not beat:
        raise HTTPException(404, "Beat not found")
    ranked = suggest_matches(song, beats)
    scored = next((r for r in ranked if r.beat_id == beat_id), None)
    song.assigned_beat_id = beat.id
    song.assigned_beat_title = beat.title
    song.match_score = scored.score if scored else None
    song.status = "ASSIGNED"
    save_songs(songs)
    return {"song": song, "assigned_beat": beat}

@app.post("/songs/{song_id}/ready")
def mark_ready(song_id: str):
    songs = load_songs()
    song = next((s for s in songs if s.id == song_id), None)
    if not song:
        raise HTTPException(404, "Song not found")
    song.status = normalize_status("READY")
    save_songs(songs)
    return {"song": song}

@app.get("/api/state")
def state():
    beats = load_beats()
    songs = load_songs()
    return {
        "template": PRODUCTION_TEMPLATE,
        "beats": beats,
        "songs": songs,
        "grid": grid_payload(songs),
    }
