(()=>{
const $=id=>document.getElementById(id),R=window.Rhythm;
let songId=null,beatId=null,lines=[],pattern=null,beats=[],songs=[],dirty=false,frame,wmVariants=[];
const section=document.createElement('section');section.className='panel';section.id='pocket';
section.innerHTML=`<h2>Write to the beat</h2>
<div class="actions"><select id="pocketSong" aria-label="Song project" style="width:auto"><option value="">Choose a saved song</option></select><button id="openPocket" type="button">Open song</button><button id="newPocket" type="button">New song</button></div>
<select id="pocketBeat" aria-label="Beat"><option value="">Choose an uploaded beat</option></select>
<audio id="pocketAudio" controls></audio>
<div class="actions"><label>BPM<input id="pBpm" type="number" min="20" max="400" value="90"></label><label>Bars<input id="pBars" type="number" min="1" max="64" value="4"></label><label>Section<input id="pSection" value="verse"></label></div>
<details><summary>Grid alignment and subdivisions</summary><div class="actions"><label>Beats per bar<input id="pMeter" type="number" min="1" max="12" value="4"></label><label>Meter denominator<select id="pDenom"><option>4</option><option>8</option><option>2</option><option>16</option></select></label><label>Subdivision<select id="pSub"><option>16</option><option>4</option><option>8</option><option>32</option><option>64</option></select></label><label>First downbeat (seconds)<input id="pOffset" type="number" min="0" step="0.01" value="0"></label><button id="alignNow" type="button">Set downbeat at playback</button></div></details>
<p class="small muted">BPM is an estimate. Confirm by listening and adjust the first downbeat. Each lyric line initially occupies one bar; blank lines are rests. Intended timing and estimated syllables do not measure your vocal performance.</p>
<div class="actions"><button id="mapPocket" type="button">Map lyrics from lyrics field</button><button id="savePocket" type="button">Save song + grid</button><button id="capturePocket" type="button">Capture flow</button><button id="applyPocket" type="button">Apply captured flow</button></div>
<div class="actions"><button id="moreSpace" type="button">Give phrases more space</button><button id="doubleTime" type="button">Double-time timing</button><button id="halfTime" type="button">Half-time timing</button></div>
<div id="pPosition" role="status" class="small"></div><div id="pTimeline" style="overflow:auto;max-height:420px"></div>
<details><summary>Edit phrase timing, syllables, stress and delivery</summary><div id="pPhrases"></div></details>
<div id="pAnalysis" class="small"></div>
<div class="card" id="warMachinePanel">
<h3>War Machine</h3>
<p class="small muted">Runs the project's defined internal engine against the saved lyric + beat grid. No paid AI provider is required. Originals stay preserved in version history.</p>
<div class="actions">
<select id="wmOperation" aria-label="War Machine operation" style="width:auto">
<option value="war_chest">War Chest — creative ammunition</option>
<option value="angel">Angel's Advocate — strengthen without replacing the thought</option>
<option value="devil">Devil's Advocate — pressure-test the move</option>
<option value="mutate">Alchemist — Mutate pocket/cadence</option>
<option value="lab">Lab in the Booth — timing review</option>
</select>
<button id="wmRun" type="button">Run War Machine</button>
</div>
<textarea id="wmRequest" rows="2" placeholder="Optional direction, e.g. pressure-test the hook or give me a less crowded pocket"></textarea>
<div id="wmStatus" class="small" role="status" aria-live="polite"></div>
<div class="actions"><select id="wmVariant" style="width:auto" disabled><option value="">No timing variant selected</option></select><button id="wmApply" type="button" disabled>Apply timing variant</button></div>
<pre id="wmOutput" class="small" style="white-space:pre-wrap;max-height:320px;overflow:auto;background:#141414;border:1px solid #333;border-radius:7px;padding:10px"></pre>
</div>
<details><summary>Beat-aware writing context</summary><select id="pRequest"><option>Write to this beat</option><option>Keep the meaning but fix the flow</option><option>Make this bar less crowded</option><option>Match the cadence of the previous bar</option><option>Move the rhyme to the end of bar 4</option><option>Give this phrase more space</option><option>Make the delivery double-time</option><option>Make this section half-time</option><option>Keep the same pocket for the next 4 bars</option><option>Create a different pocket for the hook</option></select><button id="pContext" type="button">Build writing context</button><textarea id="pPrompt" readonly rows="8" aria-label="Beat-aware writing context"></textarea><p class="small muted">The context export remains available for future model-backed writing. The War Machine controls above are real internal engine operations and do not depend on this export.</p></details>
<div id="pMessage" role="status" aria-live="polite"></div>`;
document.querySelector('.forms').after(section);
function msg(text){$('pMessage').textContent=text;}
function grid(){return R.validate({bpm:+$('pBpm').value,bars:+$('pBars').value,meter:+$('pMeter').value,denominator:+$('pDenom').value,subdivision:+$('pSub').value,offset:+$('pOffset').value,section:$('pSection').value});}
function restore(g){for(const [id,key] of [['pBpm','bpm'],['pBars','bars'],['pMeter','meter'],['pDenom','denominator'],['pSub','subdivision'],['pOffset','offset'],['pSection','section']])$(id).value=g[key];}
function esc(t){return String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function refresh(){const res=await fetch('/api/state');if(!res.ok)throw Error('Cannot load projects.');const state=await res.json();beats=state.beats;songs=state.songs;
 $('pocketSong').innerHTML='<option value="">Choose a saved song</option>'+songs.map(s=>`<option value="${s.id}">${esc(s.title)}</option>`).join('');
 $('pocketBeat').innerHTML='<option value="">Choose an uploaded beat</option>'+beats.map(b=>`<option value="${b.id}">${esc(b.title)}</option>`).join('');$('pocketSong').value=songId||'';$('pocketBeat').value=beatId||'';}
function selectBeat(id,estimate){beatId=id;const beat=beats.find(b=>b.id===id);$('pocketAudio').pause();if(beat){$('pocketAudio').src='/beats/'+id+'/audio';if(estimate){$('pBpm').value=beat.bpm>=20&&beat.bpm<=400?beat.bpm:90;$('pOffset').value=0;msg('Beat loaded. Confirm estimated BPM and downbeat by listening.');}}else {$('pocketAudio').removeAttribute('src');$('pocketAudio').load();}}
function timeline(){const g=grid(),len=R.barLength(g),total=len*g.bars,step=4/g.subdivision,scale=90;
 let h=`<div style="position:relative;width:${total*scale}px;min-height:120px;background:#151515">`;
 for(let x=0;x<=total+.001;x+=step){const bar=Math.abs(x/len-Math.round(x/len))<.001,beatUnit=4/g.denominator,major=Math.abs(x/beatUnit-Math.round(x/beatUnit))<.001;h+=`<div style="position:absolute;left:${x*scale}px;top:0;bottom:0;border-left:1px solid ${bar?'#bbb':major?'#666':'#333'}">${bar?`<span class="small">Bar ${Math.round(x/len)+1}</span>`:major?`<span class="small muted">${Math.round((x%len)/beatUnit)+1}</span>`:''}</div>`;}
 lines.forEach((l,i)=>R.tokens(l).forEach(w=>{h+=`<span title="${esc(w.text)}: ${w.syllables} estimated syllables; quarter beat ${w.start.toFixed(2)}" style="position:absolute;left:${(w.start)*scale}px;top:${28+i*32}px;width:${Math.max(10,w.duration*scale-2)}px;overflow:hidden;white-space:nowrap;background:${w.stress?'#714a22':'#28564b'};border-radius:3px">${esc(w.text)}</span>`;w.landings.forEach((b,j)=>{h+=`<span title="${esc(w.text)} syllable ${j+1}" style="position:absolute;left:${b*scale}px;top:${49+i*32}px;height:6px;border-left:2px solid ${w.stress&&j===0?'#ffc67d':'#8ed081'}"></span>`;});}));
 h+=`<div id="pCursor" style="position:absolute;top:0;bottom:0;border-left:2px solid #ff7979;pointer-events:none"></div><div style="height:${Math.max(120,lines.length*32+40)}px"></div></div>`;$('pTimeline').innerHTML=h;
 const a=R.analyze(lines,g);$('pAnalysis').innerHTML='<p>Intended flow • approximate syllable counts • no good/bad score</p>'+a.map(x=>`<p>Line ${x.line}: ${x.syllables} syllables, ~${x.rate}/second; ${x.space} quarter-beats available within a bar. ${esc(x.note)} ${x.crosses?'Crosses a bar boundary. ':''}${x.offbeat}% of intended syllables fall between quarter beats. ${x.repeated?'Repeats the preceding density/phrase length. ':''}Last word: ${esc(x.rhyme)} at beat ${x.rhymeBeat.toFixed(2)}. Delivery intent: ${esc(x.intent)}.</p>`).join('');}
function phraseEditor(){const g=grid(),len=R.barLength(g);$('pPhrases').innerHTML=lines.map((l,i)=>`<div class="card"><strong>Line ${i+1}: ${esc(l.text)||'(rest)'}</strong><div class="actions"><label>Start (quarter beats)<input data-line="${i}" data-field="start" type="number" step="0.0625" value="${l.start}"></label><label>Length (quarter beats)<input data-line="${i}" data-field="duration" type="number" min="0.0625" step="0.0625" value="${l.duration}"></label><label>Push / pull (beats)<input data-line="${i}" data-field="push" type="number" step="0.0625" value="${l.push}"></label><select data-line="${i}" data-field="intent">${['neutral','anticipation','laid-back','syncopated','double-time','half-time'].map(x=>`<option ${x===l.intent?'selected':''}>${x}</option>`).join('')}</select></div>${l.words.map((w,j)=>`<label style="display:inline-block;margin:4px">${esc(w.text)} <input style="width:55px" aria-label="Syllables in ${esc(w.text)}" type="number" min="1" max="20" data-line="${i}" data-word="${j}" data-field="syllables" value="${w.syllables}"><input style="width:auto" type="checkbox" data-line="${i}" data-word="${j}" data-field="stress" ${w.stress?'checked':''}> stress</label>`).join('')}</div>`).join('');}
function redraw(){timeline();phraseEditor();}
function safe(fn){return async()=>{try{await fn();}catch(e){msg(e.message);}};}
$('pocketBeat').onchange=safe(()=>{selectBeat($('pocketBeat').value,true);dirty=true;redraw();});
$('openPocket').onclick=safe(()=>{if(!$('stopMic').disabled)throw Error('Stop Mic before switching songs.');if(dirty&&!confirm('Discard unsaved grid edits and open another song?'))return;const song=songs.find(s=>s.id===$('pocketSong').value);if(!song)throw Error('Choose a saved song.');songId=song.id;const form=document.querySelector('#songForm');for(const k of ['title','lyrics','notes','lane','tags'])form.elements[k].value=k==='tags'?song.tags.join(', '):song[k]||'';
 const saved=song.rhythm;beatId=saved?.beat_id||song.assigned_beat_id;$('pocketBeat').value=beatId||'';selectBeat(beatId,!saved?.grid);if(saved?.grid)restore(saved.grid);lines=saved?.phrases||R.map(song.lyrics,grid());pattern=saved?.pattern||null;wmVariants=[];$('wmOutput').textContent='';$('wmStatus').textContent='';$('wmVariant').innerHTML='<option value="">No timing variant selected</option>';$('wmVariant').disabled=true;$('wmApply').disabled=true;dirty=false;redraw();msg('Song opened.');});
$('newPocket').onclick=safe(()=>{if(!$('stopMic').disabled)throw Error('Stop Mic before switching songs.');if(dirty&&!confirm('Discard unsaved edits?'))return;document.querySelector('#songForm').reset();songId=null;lines=[];pattern=null;wmVariants=[];$('wmOutput').textContent='';$('wmStatus').textContent='';$('wmVariant').innerHTML='<option value="">No timing variant selected</option>';$('wmVariant').disabled=true;$('wmApply').disabled=true;dirty=false;$('pocketSong').value='';redraw();msg('New song — enter title and lyrics above.');});
$('mapPocket').onclick=safe(()=>{lines=R.map($('lyricsBox').value,grid());dirty=true;redraw();msg('Mapped as one phrase per bar. Adjust timing and syllables to your delivery.');});
$('alignNow').onclick=safe(()=>{$('pOffset').value=$('pocketAudio').currentTime.toFixed(3);dirty=true;redraw();});
$('capturePocket').onclick=safe(()=>{if(!lines.length)throw Error('Map lyrics first.');pattern=R.capture(lines,grid());dirty=true;msg('Flow captured. Save the song to preserve it.');});
$('applyPocket').onclick=safe(()=>{lines=R.apply(lines,pattern,grid());dirty=true;redraw();msg('Applied phrase cadence. Word-level syllable counts still reflect the new lyrics.');});
function transformTiming(factor,space){if(!lines.length)throw Error('Map lyrics first.');lines=lines.map(l=>({...l,start:space?l.start:l.start*factor,duration:l.duration*factor,push:space?l.push:l.push*factor,intent:space?l.intent:factor<1?'double-time':'half-time'}));dirty=true;redraw();msg('Intended timing adjusted; words preserved. Listen and adjust phrase positions.');}
$('moreSpace').onclick=safe(()=>transformTiming(.8,true));
$('doubleTime').onclick=safe(()=>transformTiming(.5,false));
$('halfTime').onclick=safe(()=>transformTiming(2,false));
$('pContext').onclick=safe(()=>{if(!lines.length||lines.map(l=>l.text).join('\n')!==$('lyricsBox').value)throw Error('Map the current lyrics first.');$('pPrompt').value=JSON.stringify(R.context($('lyricsBox').value,lines,grid(),$('pRequest').value),null,2);msg('Beat-aware writing context built.');});
$('wmRun').onclick=safe(async()=>{
 if(!songId)throw Error('Save the song + grid before running War Machine.');
 if(dirty)throw Error('Save the current lyric + grid first so War Machine analyzes the version you see.');
 $('wmStatus').textContent='Running '+$('wmOperation').selectedOptions[0].textContent+'…';
 $('wmRun').disabled=true;
 try{
  const res=await fetch('/songs/'+songId+'/war-machine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:$('wmOperation').value,request:$('wmRequest').value})});
  const out=await res.json();
  if(!res.ok)throw Error(out.detail||'War Machine failed.');
  $('wmOutput').textContent=JSON.stringify(out.result,null,2);
  wmVariants=out.result?.result?.variants||[];
  $('wmVariant').innerHTML=wmVariants.length?'<option value="">Choose a timing alternative</option>'+wmVariants.map(v=>`<option value="${v.id}">${esc(v.label)}</option>`).join(''):'<option value="">No timing alternative returned</option>';
  $('wmVariant').disabled=!wmVariants.length;$('wmApply').disabled=true;
  $('wmStatus').textContent='Complete · run '+out.run_id+' saved to this song.';
  await refresh();
 } finally {$('wmRun').disabled=false;}
});
$('wmVariant').onchange=()=>{$('wmApply').disabled=!$('wmVariant').value;};
$('wmApply').onclick=safe(()=>{
 const variant=wmVariants.find(v=>v.id===$('wmVariant').value);
 if(!variant)throw Error('Choose a timing alternative.');
 if(variant.lyrics!==$('lyricsBox').value)throw Error('This timing alternative belongs to a different lyric version.');
 lines=JSON.parse(JSON.stringify(variant.phrases));
 dirty=true;redraw();
 msg('War Machine timing alternative applied locally. Original words are unchanged. Listen, adjust, then Save song + grid to keep it.');
});
$('pPhrases').onchange=e=>{const el=e.target;if(!el.dataset.field)return;try{const line=lines[+el.dataset.line],key=el.dataset.field;const target=el.dataset.word!==undefined?line.words[+el.dataset.word]:line;const v=key==='stress'?el.checked:key==='intent'?el.value:+el.value;if(typeof v==='number'&&(!Number.isFinite(v)||(key==='duration'&&v<=0)||(key==='syllables'&&(!Number.isInteger(v)||v<1||v>20))))throw Error('Invalid timing or syllable count.');target[key]=v;dirty=true;timeline();}catch(err){msg(err.message);phraseEditor();}};
for(const id of ['pBpm','pBars','pMeter','pDenom','pSub','pOffset','pSection'])$(id).onchange=safe(()=>{dirty=true;redraw();});
$('lyricsBox').addEventListener('input',()=>{dirty=true;if(lines.length)msg('Lyrics changed. Map the current lyrics before saving or building writing context.');});
$('pTimeline').onclick=safe(()=>{});
$('pTimeline').addEventListener('click',e=>{try{const rect=$('pTimeline').firstElementChild.getBoundingClientRect();const beat=(e.clientX-rect.left)/90;$('pocketAudio').currentTime=Math.max(0,R.seconds(beat,grid()));follow();}catch(err){msg(err.message);}});
$('savePocket').onclick=safe(async()=>{if(!beatId)throw Error('Choose a beat.');if(lines.map(l=>l.text).join('\n')!==$('lyricsBox').value)throw Error('Lyrics changed — map them before saving.');if(!lines.length)throw Error('Map lyrics first.');if(!$('stopMic').disabled)throw Error('Stop Mic and wait for final text before saving.');const form=document.querySelector('#songForm');if(!form.elements.title.value.trim())throw Error('Enter a song title.');
 if(!songId){const res=await fetch('/songs',{method:'POST',body:new FormData(form)});const out=await res.json();if(!res.ok)throw Error(out.detail||'Song save failed.');songId=out.song.id;}
 const body={lyrics:$('lyricsBox').value,title:form.elements.title.value,notes:form.elements.notes.value,lane:form.elements.lane.value,tags:form.elements.tags.value.split(',').map(t=>t.trim()).filter(Boolean),rhythm:{beat_id:beatId,grid:grid(),phrases:lines,pattern}};
 const res=await fetch('/songs/'+songId+'/rhythm',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const out=await res.json();if(!res.ok)throw Error(JSON.stringify(out.detail)||'Grid save failed.');dirty=false;await refresh();await window.load();msg('Song, beat selection, grid and flow saved.');});
function follow(){try{const g=grid(),beat=R.position($('pocketAudio').currentTime,g),len=R.barLength(g),cursor=$('pCursor');if(cursor){cursor.style.left=Math.max(0,beat)*90+'px';cursor.style.display=beat<0?'none':'block';}$('pPosition').textContent=beat<0?'Before first downbeat':`Bar ${Math.floor(beat/len)+1} · quarter beat ${(beat%len+1).toFixed(2)}`;}catch{}if(!$('pocketAudio').paused)frame=requestAnimationFrame(follow);}
$('pocketAudio').onplay=()=>{cancelAnimationFrame(frame);follow();};$('pocketAudio').onseeked=follow;$('pocketAudio').onpause=()=>{cancelAnimationFrame(frame);follow();};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
document.querySelector('#refresh').addEventListener('click',()=>refresh().catch(e=>msg(e.message)));
const originalLoad=window.load;
window.load=async function(){await originalLoad();await refresh();};
document.querySelector('#songForm').addEventListener('submit',e=>{if(songId){e.preventDefault();e.stopImmediatePropagation();$('savePocket').click();}},true);
refresh().then(redraw).catch(e=>msg(e.message));
})();
