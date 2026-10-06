"""Prepare audio-analysis compilation in the build, using synthetic audio only."""
import tempfile
from pathlib import Path
import numpy as np
import soundfile as sf
from .analyzer import analyze_beat, analyze_performance

def main():
    rate = 22050
    time = np.arange(8 * rate) / rate
    audio = (.08 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
    for tick in np.arange(0, 8, .5):
        start = int(tick * rate)
        audio[start:start+200] += np.linspace(.5, 0, 200, dtype=np.float32)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'synthetic.wav'
        sf.write(path, audio, rate)
        analyze_beat(str(path), 'warmup', 'Synthetic build warmup')
        analyze_performance(str(path))
    print('Audio analysis warmup complete.')

if __name__ == '__main__':
    main()
