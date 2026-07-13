import os
import glob
import tempfile
import ffmpeg
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
    }
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        caption = info_dict.get('description', '')
        # Get the downloaded file path
        # It's possible the extension is mp4 or something else
        video_id = info_dict.get('id')
        video_ext = info_dict.get('ext')
        video_path = os.path.join(output_dir, f"{video_id}.{video_ext}")
        return video_path, caption

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
        try:
            video_path, caption = download_reel(url, temp_dir)
        except Exception as e:
            print(f"Failed to download {url}: {e}")
            return None, None
            
        print("Extracting keyframes...")
        extract_keyframes(video_path, temp_dir)
        
        print("Transcribing audio...")
        try:
            transcript = transcribe_audio(video_path)
        except Exception as e:
            print(f"Failed to transcribe {url}: {e}")
            transcript = ""
            
        return caption, transcript
