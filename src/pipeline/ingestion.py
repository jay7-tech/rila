import os
import glob
import tempfile
import ffmpeg
import re
import html
from yt_dlp import YoutubeDL
from faster_whisper import WhisperModel

def download_reel(url: str, output_dir: str):
    """
    Downloads the video and extracts the caption using yt-dlp.
    Returns (video_path, caption).
    """
    ydl_opts = {
        'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'subtitlesformat': 'vtt',
    }
    
    cookies_path = os.environ.get("INSTAGRAM_COOKIES_PATH")
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path
        print(f"Using cookies from: {cookies_path}")
        
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        caption = info_dict.get('description', '')
        
        video_id = info_dict.get('id')
        video_ext = info_dict.get('ext')
        video_path = os.path.join(output_dir, f"{video_id}.{video_ext}")
        
        # Check for subtitles
        sub_path = None
        for file in glob.glob(os.path.join(output_dir, f"{video_id}*.vtt")):
            sub_path = file
            break
            
        return video_path, caption, sub_path

def parse_vtt(vtt_path: str) -> str:
    """Parses a VTT file, removing timestamps and deduplicating overlapping lines."""
    with open(vtt_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    cleaned_lines = []
    # match timestamps like 00:00:00.000 --> 00:00:02.000
    timestamp_pattern = re.compile(r'^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}')
    tag_pattern = re.compile(r'<[^>]+>')
    
    for line in lines:
        line = line.strip()
        if not line or line.upper() == 'WEBVTT' or line.startswith('Kind:') or line.startswith('Language:') or line.startswith('Style:'):
            continue
        if timestamp_pattern.match(line):
            continue
        
        # remove tags and alignment info occasionally added to cue text
        line = html.unescape(line)
        line = tag_pattern.sub('', line)
        line = line.replace('align:start position:0%', '')
        line = line.strip()
        
        if line:
            # Deduplicate consecutive identical lines
            if not cleaned_lines or cleaned_lines[-1] != line:
                cleaned_lines.append(line)
                
    return " ".join(cleaned_lines)

def extract_keyframes(video_path: str, output_dir: str):
    """
    Extracts 3-5 keyframes from the video using ffmpeg.
    We'll just extract a frame every few seconds depending on duration.
    For simplicity, extract 3 frames evenly spaced.
    """
    try:
        probe = ffmpeg.probe(video_path)
        duration = float(probe['format']['duration'])
        # Extract at 10%, 50%, 90% of the video
        timestamps = [duration * 0.1, duration * 0.5, duration * 0.9]
        
        for i, ts in enumerate(timestamps):
            out_path = os.path.join(output_dir, f"frame_{i}.jpg")
            (
                ffmpeg
                .input(video_path, ss=ts)
                .filter('scale', 640, -1)
                .output(out_path, vframes=1, loglevel='error')
                .run(overwrite_output=True)
            )
    except Exception as e:
        print(f"Keyframe extraction failed: {e}")

def transcribe_audio(video_path: str) -> str:
    """
    Transcribes the audio of the video using faster-whisper.
    cpu, int8 for speed and memory efficiency.
    """
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(video_path, beam_size=5)
    
    transcript = ""
    for segment in segments:
        transcript += segment.text + " "
    
    return transcript.strip()

def process_reel(url: str):
    """
    Full ingestion pipeline.
    Returns (caption, transcript).
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Downloading {url} to {temp_dir}...")
        video_path, caption, sub_path = download_reel(url, temp_dir)
            
        print("Extracting keyframes...")
        extract_keyframes(video_path, temp_dir)
        
        if sub_path and os.path.exists(sub_path):
            print("Transcript source: YouTube captions")
            try:
                transcript = parse_vtt(sub_path)
            except Exception as e:
                print(f"Failed to parse VTT {sub_path}: {e}")
                transcript = ""
        else:
            print("Transcript source: Whisper (no captions available)")
            print("Transcribing audio...")
            try:
                transcript = transcribe_audio(video_path)
            except Exception as e:
                print(f"Failed to transcribe {url}: {e}")
                transcript = ""
            
        return caption, transcript
