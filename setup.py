from setuptools import setup, find_packages

setup(
    name="xdu_summarizer",
    version="1.0.0",
    description="XDU 网课自动下载 + 智能总结系统 — 图文并茂的课程笔记",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "openai-whisper>=20231117",
        "opencv-python>=4.8.0",
        "openai>=1.0.0",
        "pillow>=10.0.0",
        "tqdm>=4.0.0",
        "numpy>=1.24.0",
        "requests>=2.32.5",
        "beautifulsoup4>=4.13.5",
        "pycryptodome>=3.23.0",
        "psutil>=7.0.0",
        "flask>=3.0.0",
        "funasr>=1.3.9",
        "modelscope>=1.37.1",
        "transformers>=4.51.3,<5.0.0",
        "soundfile>=0.13.1",
        "librosa>=0.11.0",
        "python-dotenv>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "xdu-summarizer=xdu_summarizer.cli:main",
            "xdu-summarizer-web=xdu_summarizer.webui:main",
        ],
    },
)
