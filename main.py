import asyncio
import json
import os

from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from pdf_parser import process_pdf

app = FastAPI(title="PDF to LLM-Optimized XML Compressor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gap 3: hard limit on uploaded file size
MAX_FILE_MB    = 20
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ── Gap 3 + 4: original endpoint (kept for compatibility) ─────
@app.post("/api/v1/compress")
async def compress_pdf(
    file: UploadFile = File(...),
    dictCompression: str = Form("true"),
    semanticDeduplication: str = Form("true"),
    minifyXml: str = Form("true"),
    chunkSize: int = Form(800),          # Gap 4
):
    content = await file.read()

    # Gap 3: size guard
    if len(content) > MAX_FILE_BYTES:
        return JSONResponse(
            content={"error": f"File too large — maximum is {MAX_FILE_MB} MB."},
            status_code=413,
        )

    try:
        # Gap 3: offload heavy CPU work to a thread pool
        xml_content, orig_tokens, comp_tokens, chunks = await asyncio.to_thread(
            process_pdf,
            content,
            use_dict=(dictCompression == "true"),
            dedup_headers=(semanticDeduplication == "true"),
            minify=(minifyXml == "true"),
            chunk_token_limit=chunkSize,
        )
        return JSONResponse(content={
            "filename": file.filename,
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "xml_content": xml_content,
            "chunks": chunks,
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ── Gap 5: SSE streaming endpoint ────────────────────────────
@app.post("/api/v1/compress/stream")
async def compress_pdf_stream(
    file: UploadFile = File(...),
    dictCompression: str = Form("true"),
    semanticDeduplication: str = Form("true"),
    minifyXml: str = Form("true"),
    chunkSize: int = Form(800),
):
    content = await file.read()
    filename = file.filename

    if len(content) > MAX_FILE_BYTES:
        async def _err():
            yield f"data: {json.dumps({'type':'error','message':f'File too large — maximum is {MAX_FILE_MB} MB.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    def progress_cb(stage: str):
        # Called from worker thread — put_nowait is thread-safe
        queue.put_nowait(stage)

    async def _worker():
        try:
            result = await asyncio.to_thread(
                process_pdf,
                content,
                use_dict=(dictCompression == "true"),
                dedup_headers=(semanticDeduplication == "true"),
                minify=(minifyXml == "true"),
                chunk_token_limit=chunkSize,
                progress_cb=progress_cb,
            )
            queue.put_nowait(("result", result, filename))
        except Exception as exc:
            queue.put_nowait(("error", str(exc)))

    async def event_stream():
        task = asyncio.create_task(_worker())
        while True:
            item = await queue.get()
            if isinstance(item, tuple):
                kind = item[0]
                if kind == "result":
                    _, (xml, orig, comp, chunks), fname = item
                    payload = {
                        "type": "done",
                        "filename": fname,
                        "original_tokens": orig,
                        "compressed_tokens": comp,
                        "xml_content": xml,
                        "chunks": chunks,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    break
                else:  # error
                    yield f"data: {json.dumps({'type':'error','message':item[1]})}\n\n"
                    break
            else:
                # plain progress stage string
                yield f"data: {json.dumps({'type':'progress','stage':item})}\n\n"
        await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    is_local = os.environ.get("PORT") is None
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=is_local)
