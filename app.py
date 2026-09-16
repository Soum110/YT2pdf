import os
import gradio as gr
from main import app as fastapi_app

# Interactive dashboard for Hugging Face Space UI
with gr.Blocks(title="YT2PDF API Server", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 📑 YT2PDF Video Conversion Backend
    This Space is the 24/7 high-performance processing engine powering [yt2pdfs.com](https://yt2pdfs.com).
    
    ### API Status: **Online & Ready**
    - **Memory**: 16 GB High-Performance RAM
    - **Features**: OpenCV SSIM Frame Filtering, Gemini Multimodal AI, PDF Generation
    - **Heartbeat**: Cloudflare Workers Cron (Kept Awake 24/7)
    """)

# Mount Gradio demo on the root or /gradio, while keeping all /api/* routes active
app = gr.mount_gradio_app(fastapi_app, demo, path="/")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
