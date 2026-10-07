from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone

STOP_WORDS = {
    "a","an","and","are","as","at","be","been","but","by","for","from","had","has","have",
    "he","her","him","his","i","if","in","is","it","its","me","my","of","on","or","our",
    "she","so","that","the","their","them","they","this","to","was","we","were","with",
    "you","your"
}

RHYME_FAMILIES = {
    "ight": ["light","night","sight","bright","flight","tight","write","white"],
    "ay": ["day","way","say","play","stay","pay","spray","gray"],
    "ed": ["head","bed","fed","dead","red","said"],
    "ore": ["more","floor","door","shore","core","war"],
    "air": ["air","care","rare","stare","wear","glare","pair"],
    "ame": ["name","game","flame","frame","same","claim"],
    "old": ["cold","gold","hold","told","fold","sold"],
    "own": ["down","town","crown","brown","drown","around"],
    "ain": ["rain","pain","chain","train","gain","lane"],
    "ear": ["fear","near","clear","year","gear","appear"],
    "ack": ["back","black","stack","track","crack","pack"],
    "ide": ["ride","side","wide","hide","slide","tide"],
    "ow": ["flow","show","slow","glow","low","know"],
}

DOMAIN_TERMS = {
    "pressure": {"pressure","press","weight","heavy","crush","squeeze","load","stress"},
    "temperature": {"cold","ice","freeze","frozen","heat","hot","fire","burn","flame"},
    "motion": {"run","walk","drive","ride","slide","fall","rise","move","spin","turn"},
    "money": {"money","cash","peso","dollar","gold","rich","wealth","bank","paid","pay"},
    "street": {"street","block","corner","trap","plug","bag","scale","brick","cell"},
    "ocean": {"sea","ocean","wave","shore","tide","island","reef","boat","salt"},
    "light": {"light","dark","shadow","glow","shine","night","bright","black"},
    "music": {"beat","bar","flow","mic","booth","track","song","verse","hook","rhyme"},
    "body": {"blood","heart","bone","skin","breath","hand","eye","head","soul"},
}

PHYSICAL_PATHS = {
    "pressure": ["compression","release","leak","burst","load-bearing","vacuum"],
    "temperature": ["melt","freeze","steam","frost","scorch","temperature shock"],
    "motion": ["acceleration","brake","swerve","drift","collision","momentum"],
    "money": ["count","stack","exchange","interest","debt","counterfeit"],
    "street": ["corner","route","traffic","surveillance","stash","exit"],
    "ocean": ["current","undertow","depth","buoyancy","storm","shoreline"],
    "light": ["reflection","silhouette","glare","eclipse","beam","blind spot"],
    "music": ["bar","rest","downbeat","echo","feedback","drop","refrain"],
    "body": ["pulse","scar","breath","weight","reflex","wound"],
}

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def words(text: str):
    return re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?", text or "")

def syllable_guess(word: str):
    cleaned = re.sub(r"[^a-z]", "", word.lower())
    if not cleaned:
        return 1
    cleaned = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", cleaned)
    groups = re.findall(r"[aeiouy]+", cleaned)
    return max(1, len(groups))

def rhyme_key(word: str):
    w = re.sub(r"[^a-z]", "", word.lower())
    for suffix in sorted(RHYME_FAMILIES, key=len, reverse=True):
        if w.endswith(suffix):
            return suffix
    vowels = [m.start() for m in re.finditer(r"[aeiouy]", w)]
    if vowels:
        return w[vowels[-1]:]
    return w[-3:]

def sound_neighborhood(word: str):
    key = rhyme_key(word)
    if key in RHYME_FAMILIES:
        return {"anchor": word, "gravity_key": key, "neighbors": RHYME_FAMILIES[key]}
    w = re.sub(r"[^a-z]", "", word.lower())
    return {
        "anchor": word,
        "gravity_key": key,
        "neighbors": [w, f"{w}ing", f"{w}er", f"{w}ed"] if w else [],
        "note": "Fallback spelling/sound proxy; no pronunciation dictionary is configured.",
    }

def grid_context(song, beat):
    rhythm = song.rhythm or {}
    grid = rhythm.get("grid") or {}
    return {
        "beat_id": rhythm.get("beat_id"),
        "beat_title": getattr(beat, "title", None),
        "bpm": grid.get("bpm"),
        "meter": f"{grid.get('meter', 4)}/{grid.get('denominator', 4)}",
        "subdivision": grid.get("subdivision"),
        "section": grid.get("section"),
        "first_downbeat_seconds": grid.get("offset"),
        "timing_basis": "user-edited intended lyric timing; not measured word timing from recorded vocals",
    }

def _bar_length(grid):
    denominator = float(grid.get("denominator") or 4)
    return float(grid.get("meter") or 4) * 4.0 / denominator

def line_metrics(song):
    rhythm = song.rhythm or {}
    grid = rhythm.get("grid") or {}
    bpm = float(grid.get("bpm") or 90)
    bar = _bar_length(grid)
    rows = []
    for index, phrase in enumerate(rhythm.get("phrases") or []):
        phrase_words = phrase.get("words") or []
        counts = [max(1, int(w.get("syllables") or syllable_guess(w.get("text", "")))) for w in phrase_words]
        total = sum(counts)
        duration = max(0.0625, float(phrase.get("duration") or bar * 0.8))
        push = float(phrase.get("push") or 0)
        start = float(phrase.get("start") or 0) + push
        seconds = duration * 60.0 / bpm
        rate = total / max(seconds, 0.001)
        elapsed = 0
        landings = []
        for count in counts:
            for n in range(count):
                landings.append(start + (elapsed + n) * duration / max(total, 1))
            elapsed += count
        offbeat = sum(1 for x in landings if abs(x - round(x)) > 0.08)
        final = phrase_words[-1].get("text", "") if phrase_words else ""
        before_final = sum(counts[:-1])
        final_start = start + before_final * duration / max(total, 1)
        end = start + duration
        rows.append({
            "line": index + 1,
            "text": phrase.get("text", ""),
            "syllables": total,
            "rate_syllables_per_second": round(rate, 2),
            "start_quarter_beats": round(start, 3),
            "duration_quarter_beats": round(duration, 3),
            "available_bar_space_quarter_beats": round(max(0.0, bar - duration), 3),
            "crosses_bar": int((end - 0.001) // bar) > int(start // bar) if bar else False,
            "off_quarter_percent": round((offbeat / len(landings) * 100) if landings else 0),
            "final_word": final,
            "final_word_landing_quarter_beat": round(final_start, 3),
            "rhyme_key": rhyme_key(final) if final else "",
            "stressed_words": [w.get("text") for w in phrase_words if w.get("stress")],
            "delivery_intent": phrase.get("intent", "neutral"),
        })
    for i, row in enumerate(rows):
        previous = rows[i - 1] if i else None
        row["repeats_previous_density"] = bool(
            previous
            and previous["syllables"] == row["syllables"]
            and abs(previous["duration_quarter_beats"] - row["duration_quarter_beats"]) < 0.125
        )
    return rows

def cadence_slots(song):
    slots = []
    for metric in line_metrics(song):
        slots.append({
            "line": metric["line"],
            "syllables": metric["syllables"],
            "start": metric["start_quarter_beats"],
            "duration": metric["duration_quarter_beats"],
            "stresses": metric["stressed_words"],
            "final_word": metric["final_word"],
            "final_landing": metric["final_word_landing_quarter_beat"],
            "delivery": metric["delivery_intent"],
        })
    return slots

def detected_domains(text: str):
    token_set = {w.lower() for w in words(text)}
    found = []
    for name, terms in DOMAIN_TERMS.items():
        hits = sorted(token_set & terms)
        if hits:
            found.append({"domain": name, "hits": hits, "physical_paths": PHYSICAL_PATHS[name]})
    return found

def focus_terms(text: str):
    candidates = [w.lower() for w in words(text) if w.lower() not in STOP_WORDS and len(w) > 2]
    counts = {}
    for word in candidates:
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[:8]]

def timing_variant(song, mode: str):
    rhythm = song.rhythm or {}
    grid = rhythm.get("grid") or {}
    phrases = copy.deepcopy(rhythm.get("phrases") or [])
    step = 4.0 / float(grid.get("subdivision") or 16)
    bar = _bar_length(grid)
    if mode == "air":
        for phrase in phrases:
            phrase["duration"] = round(max(0.0625, float(phrase.get("duration") or 1) * 0.82), 4)
    elif mode == "syncopated":
        for index, phrase in enumerate(phrases):
            phrase["push"] = round(float(phrase.get("push") or 0) + (step / 2 if index % 2 == 0 else -step / 2), 4)
            phrase["intent"] = "syncopated"
    elif mode == "late_landing":
        for phrase in phrases:
            phrase_words = phrase.get("words") or []
            total = sum(max(1, int(w.get("syllables") or 1)) for w in phrase_words)
            before_final = sum(max(1, int(w.get("syllables") or 1)) for w in phrase_words[:-1])
            if total and before_final:
                start = float(phrase.get("start") or 0) + float(phrase.get("push") or 0)
                current_bar = int(max(0.0, start) // bar) if bar else 0
                target = (current_bar + 1) * bar - step
                duration = (target - start) * total / before_final
                phrase["duration"] = round(max(0.125, min(bar * 1.5, duration)), 4)
    else:
        raise ValueError("Unknown timing variant")
    return phrases

def war_chest(song):
    metrics = line_metrics(song)
    finals = [m["final_word"] for m in metrics if m["final_word"]]
    domains = detected_domains(song.lyrics)
    terms = focus_terms(song.lyrics)
    return {
        "purpose": "creative ammunition; not a finished song or silent rewrite",
        "focus_terms": terms,
        "sound_gravity": [sound_neighborhood(word) for word in finals[-6:]],
        "physical_pathways": domains or [{
            "domain": "generic physical dimensions",
            "hits": terms[:4],
            "physical_paths": ["weight","temperature","motion","texture","pressure","distance"],
        }],
        "metaphor_crossovers": [
            {
                "from": domains[i]["domain"],
                "to": domains[(i + 1) % len(domains)]["domain"],
                "instruction": f"Cross {domains[i]['domain']} mechanics with {domains[(i + 1) % len(domains)]['domain']} imagery without changing the song's core claim.",
            }
            for i in range(len(domains))
        ] if len(domains) > 1 else [
            {"instruction": "Take one literal object/action from the lyric and map its physical behavior onto the emotional or narrative claim."},
            {"instruction": "Invert one image: show the consequence first, then reveal the cause."},
        ],
        "cadence_slots": cadence_slots(song),
        "hook_paths": [
            "Repeat or echo an existing short phrase; do not invent a new thesis.",
            "Use the strongest final-word sound family as a hook anchor.",
            "Leave one intentional rest before the payoff instead of adding more words.",
        ],
    }

def angel(song):
    rows = line_metrics(song)
    notes = []
    for row in rows:
        strengths = []
        moves = []
        if row["stressed_words"]:
            strengths.append("explicit stress map")
        if row["off_quarter_percent"] >= 30:
            strengths.append("syncopated placement")
        if row["available_bar_space_quarter_beats"] >= 0.75:
            strengths.append("breathing room")
        if row["final_word"]:
            strengths.append(f"clear final-word anchor: {row['final_word']}")
        if row["rate_syllables_per_second"] > 6:
            moves.append("shorten or split the phrase timing before changing any words")
        if row["repeats_previous_density"]:
            moves.append("keep the thought; vary push/pull or stress so the repeated density feels intentional")
        if not row["stressed_words"] and row["final_word"]:
            moves.append("audition stress on the final word or the word carrying the claim")
        if not moves:
            moves.append("preserve the current wording and audition delivery changes only")
        notes.append({"line": row["line"], "strengths": strengths, "strengthening_moves": moves})
    return {
        "purpose": "preserve the thought and strengthen delivery/cadence",
        "line_notes": notes,
        "protected_lyrics": song.lyrics,
        "timing_variant": {
            "label": "Angel — more air",
            "lyrics": song.lyrics,
            "phrases": timing_variant(song, "air"),
            "timing_basis": "intended timing alternative; original words preserved",
        },
    }

def devil(song):
    rows = line_metrics(song)
    critiques = []
    seen_rhymes = {}
    for row in rows:
        issues = []
        alternatives = []
        key = row["rhyme_key"]
        if row["rate_syllables_per_second"] > 6:
            issues.append("delivery is mechanically dense")
            alternatives.append("attack the setup earlier or create a rest; do not solve density by automatically deleting the idea")
        if row["repeats_previous_density"]:
            issues.append("same density/phrase length as the prior line")
            alternatives.append("change cadence shape, not necessarily the lyric")
        if key and seen_rhymes.get(key, 0) >= 2:
            issues.append(f"rhyme gravity is clustering on '{key}'")
            alternatives.append("switch the sound family for one line or delay the expected rhyme")
        seen_rhymes[key] = seen_rhymes.get(key, 0) + 1
        if row["off_quarter_percent"] == 0 and row["syllables"] >= 8:
            alternatives.append("audition one intentional anticipation or late entry so the line is not locked to every quarter beat")
        if issues or alternatives:
            critiques.append({"line": row["line"], "pressure_test": issues or ["no obvious mechanical weakness detected"], "alternate_strategy": alternatives or ["change image domain before changing wording"]})
    return {
        "purpose": "attack forced, obvious or repeated moves and offer a different strategy",
        "critique": critiques or [{"pressure_test": ["no mechanical red flag detected from timing alone"], "alternate_strategy": ["test a different metaphor/setup before rewriting the line"]}],
        "protected_lyrics": song.lyrics,
        "rule": "critique is advisory; it never overwrites the artist's lyric",
    }

def mutate(song):
    return {
        "purpose": "mutate cadence/pocket while preserving the artist's exact words",
        "protected_lyrics": song.lyrics,
        "variants": [
            {"id": "air", "label": "Air / compression release", "lyrics": song.lyrics, "phrases": timing_variant(song, "air"), "timing_basis": "intended timing"},
            {"id": "syncopated", "label": "Push / pull mutation", "lyrics": song.lyrics, "phrases": timing_variant(song, "syncopated"), "timing_basis": "intended timing"},
            {"id": "late_landing", "label": "Final-word landing mutation", "lyrics": song.lyrics, "phrases": timing_variant(song, "late_landing"), "timing_basis": "intended timing"},
        ],
        "strategy_mutations": [
            "literal → figurative: preserve the claim, change the image pathway",
            "figurative → literal: state the consequence plainly, then restore imagery",
            "setup/payoff inversion: reveal the consequence before the cause",
            "emotional-lane mutation: keep facts intact, change restraint/intensity in delivery rather than biography",
        ],
    }

def lab(song):
    recorded = {
        "has_recorded_audio": bool(song.audio_path),
        "word_level_timing_measured": False,
        "timing_alignment_status": "not measured",
    }
    if song.audio_path:
        recorded.update({
            "measured_audio_duration_seconds": song.duration,
            "measured_performance_bpm_estimate": song.estimated_bpm,
            "measured_energy": song.energy,
            "measured_onset_density": song.onset_density,
            "measured_pause_ratio": song.pause_ratio,
            "note": "These are file-level performance measurements. They are not lyric-word timestamps.",
        })
    return {
        "purpose": "Lab in the Booth timing/pocket review",
        "intended_lyric_timing": line_metrics(song),
        "recorded_vocal_measurements": recorded,
        "separation_rule": "Never label intended grid timing as measured vocal alignment.",
    }

def run_engine(song, beat, operation: str, request: str = ""):
    operations = {
        "war_chest": war_chest,
        "angel": angel,
        "devil": devil,
        "mutate": mutate,
        "lab": lab,
    }
    if operation not in operations:
        raise ValueError("Unknown War Machine operation")
    result = operations[operation](song)
    return {
        "engine": "war_machine",
        "operation": operation,
        "status": "complete",
        "request": request.strip(),
        "context": grid_context(song, beat),
        "result": result,
    }

def record_version(song, kind: str, label: str, *, lyrics=None, rhythm=None, source_run_id=None):
    text = song.lyrics if lyrics is None else lyrics
    timing = copy.deepcopy(song.rhythm if rhythm is None else rhythm)
    versions = song.lyric_versions
    if versions:
        last = versions[-1]
        if last.get("kind") == kind and last.get("lyrics") == text and last.get("rhythm") == timing and last.get("label") == label:
            return last
    version = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "label": label,
        "lyrics": text,
        "rhythm": timing,
        "created_at": utc_now(),
    }
    if source_run_id:
        version["source_run_id"] = source_run_id
    versions.append(version)
    if len(versions) > 50:
        original = next((v for v in versions if v.get("kind") == "original"), versions[0])
        versions[:] = [original] + [v for v in versions[-49:] if v is not original]
    return version
