/* Intended lyric timing, not measured vocal performance. Quarter note = one beat. */
(function(root){
  const syllables=word=>Math.max(1,(word.toLowerCase().replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/,'').match(/[aeiouy]+/g)||[]).length);
  function validate(g){
    if(!Number.isFinite(g.bpm)||g.bpm<20||g.bpm>400)throw Error('BPM must be between 20 and 400.');
    if(!Number.isInteger(g.meter)||g.meter<1||g.meter>12)throw Error('Beats per bar must be 1–12.');
    if(![2,4,8,16].includes(g.denominator))throw Error('Invalid meter denominator.');
    if(![4,8,16,32,64].includes(g.subdivision))throw Error('Choose a supported subdivision.');
    if(!Number.isFinite(g.offset)||g.offset<0)throw Error('First downbeat must be at or after zero.');
    if(!Number.isInteger(g.bars)||g.bars<1||g.bars>64)throw Error('Use 1–64 bars.');
    return g;
  }
  const barLength=g=>g.meter*4/g.denominator;
  const seconds=(beat,g)=>g.offset+beat*60/g.bpm;
  const position=(time,g)=>(time-g.offset)*g.bpm/60;
  function map(lyrics,g){
    validate(g);const length=barLength(g);
    return lyrics.split(/\n/).map((text,i)=>({text,start:i*length,duration:length*.8,push:0,intent:'neutral',
      words:(text.match(/[\p{L}\p{N}]+(?:['’][\p{L}]+)*/gu)||[]).map(text=>({text,syllables:syllables(text),stress:false}))}));
  }
  function tokens(line){
    const total=line.words.reduce((n,w)=>n+w.syllables,0);let slot=0;
    return line.words.map(w=>{const start=line.start+line.push+slot*line.duration/Math.max(1,total);slot+=w.syllables;
      return {...w,start,duration:w.syllables*line.duration/Math.max(1,total),landings:Array.from({length:w.syllables},(_,i)=>start+i*line.duration/Math.max(1,total))};});
  }
  function analyze(lines,g){
    validate(g);const length=barLength(g),step=4/g.subdivision;
    return lines.map((line,i)=>{
      const count=line.words.reduce((n,w)=>n+w.syllables,0), rate=count/Math.max(.001,line.duration*60/g.bpm);
      const end=line.start+line.push+line.duration;
      const lands=tokens(line).flatMap(w=>w.landings);
      const off=lands.filter(x=>Math.abs(x-Math.round(x))>.08).length;
      const previous=lines[i-1];
      return {line:i+1,syllables:count,rate:+rate.toFixed(1),space:+Math.max(0,length-line.duration).toFixed(2),
        start:+(line.start+line.push).toFixed(3),end:+end.toFixed(3),crosses:Math.floor((end-.001)/length)>Math.floor((line.start+line.push)/length),
        rhyme:line.words.at(-1)?.text||'',rhymeBeat:tokens(line).at(-1)?.start??line.start,
        offbeat:lands.length?Math.round(off/lands.length*100):0,
        repeated:!!previous&&previous.words.reduce((n,w)=>n+w.syllables,0)===count&&Math.abs(previous.duration-line.duration)<step/2,
        note:rate>6?'Dense delivery: try more space or fewer syllables.':rate>4?'Fast delivery: check breath and articulation.':'Room to shape the delivery.',
        intent:line.intent,stressBeats:tokens(line).filter(w=>w.stress).map(w=>w.start)};
    });
  }
  function capture(lines,g){return {barLength:barLength(g),phrases:lines.map(l=>({start:l.start,duration:l.duration,push:l.push,syllables:l.words.map(w=>w.syllables),stress:l.words.map(w=>w.stress),intent:l.intent}))};}
  function apply(lines,pattern,g){
    if(!pattern?.phrases?.length)throw Error('Capture a flow first.');
    if(pattern.barLength!==barLength(g))throw Error('Pattern meter differs. Use the same meter first.');
    const span=Math.max(...pattern.phrases.map(l=>l.start+l.duration));const cycle=Math.ceil(span/barLength(g))*barLength(g);
    return lines.map((l,i)=>{const p=pattern.phrases[i%pattern.phrases.length];return {...l,start:p.start+Math.floor(i/pattern.phrases.length)*cycle,duration:p.duration,push:p.push,intent:p.intent};});
  }
  function context(lyrics,lines,g,request){return {task:request,lyrics,grid:validate(g),bpmUnit:'quarter note',timingBasis:'user-editable intended delivery; syllable estimates; not recorded performance',phrases:lines,pocket:analyze(lines,g),instructions:'Preserve meaning unless asked otherwise. Respect phrase starts, rests, rhyme targets, section and meter. Return lyrics and intended beat positions; explain tradeoffs. Syncopation and push/pull are valid; do not force every syllable onto a beat.'};}
  const api={syllables,validate,barLength,seconds,position,map,tokens,analyze,capture,apply,context};
  if(typeof module!=='undefined')module.exports=api;else root.Rhythm=api;
})(typeof window==='undefined'?globalThis:window);
