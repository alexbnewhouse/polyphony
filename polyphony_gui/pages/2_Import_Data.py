"""
Polyphony GUI — Import Data
============================
Upload documents, audio, podcasts, and RSS feeds for coding.
"""

import logging
import tempfile
from pathlib import Path

import streamlit as st

from polyphony_gui.components import render_sidebar, require_project, render_segment
from polyphony_gui.db import (
    get_documents,
    get_segments_preview,
    update_project_status,
)
from polyphony_gui.services import (
    MAX_FILE_SIZE_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    validate_upload_sizes,
    safe_error_message,
)

logger = logging.getLogger("polyphony_gui")

st.set_page_config(page_title="Import Data — Polyphony", page_icon="📄", layout="wide")
render_sidebar()

# ─── Guard ────────────────────────────────────────────────────────────────────
p, db_path, project_id = require_project()
project_dir = Path(str(db_path)).parent

# ─── Page ─────────────────────────────────────────────────────────────────────
st.title("📄 Import Data")
st.markdown(f"**Project:** {p['name']}")

# ── Existing documents ────────────────────────────────────────────────────────
docs = get_documents(db_path, project_id)
if docs:
    st.markdown(f"### Corpus ({len(docs)} document{'s' if len(docs) != 1 else ''})")

    import pandas as pd
    df = pd.DataFrame([{
        "Filename": d["filename"],
        "Type": d.get("media_type", "text").title(),
        "Segments": d.get("segment_count", "—"),
        "Words": d.get("word_count") or "—",
        "Imported": (d.get("created_at") or "")[:16],
    } for d in docs])
    st.dataframe(df, use_container_width=True, hide_index=True)

    with st.expander("Preview first 20 segments"):
        segs = get_segments_preview(db_path, project_id, limit=20)
        for s in segs:
            render_segment(s, truncate=200)

    st.divider()

# ─── Tabbed import interface ─────────────────────────────────────────────────
tab_files, tab_audio, tab_podcast, tab_rss = st.tabs([
    "📁 Files",
    "🎙️ Audio / Transcription",
    "🎧 Podcast",
    "📡 RSS Feed",
])

# ── Files tab ─────────────────────────────────────────────────────────────────
with tab_files:
    st.markdown("### Upload Documents")
    st.markdown(
        "Supported formats: **plain text** (.txt), **Word** (.docx), **CSV**, **JSON**, "
        "and **images** (.png, .jpg, etc.)"
    )
    st.caption(
        f"Maximum {MAX_FILE_SIZE_BYTES // (1024*1024)} MB per file, "
        f"{MAX_TOTAL_UPLOAD_BYTES // (1024*1024)} MB total."
    )

    with st.form("import_form"):
        uploaded_files = st.file_uploader(
            "Choose files to upload",
            accept_multiple_files=True,
            type=["txt", "md", "docx", "csv", "json", "png", "jpg", "jpeg", "gif", "webp", "bmp"],
            help="You can select multiple files at once.",
        )

        st.markdown("**Segmentation Strategy**")
        st.caption(
            "Segments are the units that get coded. Choose how to split your documents."
        )
        strategy_choice = st.radio(
            "Split documents into segments by:",
            options=["paragraph", "sentence", "manual", "fixed"],
            format_func=lambda x: {
                "paragraph": "📝 Paragraph — split on blank lines (recommended for interviews)",
                "sentence": "📋 Sentence — split on sentence boundaries (good for short responses)",
                "manual": "📄 Manual — each file is one segment (good for short documents)",
                "fixed": "📏 Fixed window — split into fixed-size word chunks",
            }[x],
            index=0,
            label_visibility="collapsed",
            help=(
                "**Paragraph** works well for most qualitative data (interviews, field notes, essays). "
                "**Sentence** is best for short responses (surveys, social media). "
                "**Manual** treats each file as a single codeable unit. "
                "**Fixed** splits into N-word chunks — useful for very long, unstructured text."
            ),
        )

        window_size = None
        if strategy_choice == "fixed":
            window_size = st.number_input("Words per window", min_value=10, max_value=1000, value=100)

        content_col = st.text_input(
            "CSV content column name",
            value="content",
            help="For CSV files: which column contains the text to be coded?",
        )

        submitted = st.form_submit_button("Import Files", type="primary")

    # Segmentation preview on file upload
    if uploaded_files and not submitted:
        preview_file = uploaded_files[0]
        if preview_file.name.lower().endswith((".txt", ".md")):
            raw_text = preview_file.read().decode("utf-8", errors="replace")
            preview_file.seek(0)  # Reset for later import

            # Simple preview segmentation
            if strategy_choice == "paragraph":
                segments = [s.strip() for s in raw_text.split("\n\n") if s.strip()]
            elif strategy_choice == "sentence":
                import re
                segments = [s.strip() for s in re.split(r'(?<=[.!?])\s+', raw_text) if s.strip()]
            elif strategy_choice == "fixed" and window_size:
                words = raw_text.split()
                segments = [" ".join(words[i:i+window_size])
                            for i in range(0, len(words), window_size)]
            else:
                segments = [raw_text.strip()] if raw_text.strip() else []

            if segments:
                st.info(
                    f"**Preview:** '{preview_file.name}' would produce **{len(segments)}** segments "
                    f"with {strategy_choice} segmentation."
                )
                with st.expander("Preview first 5 segments"):
                    for i, seg in enumerate(segments[:5]):
                        st.markdown(f"**Segment {i+1}:**")
                        preview = (seg[:200] + "…") if len(seg) > 200 else seg
                        st.caption(preview)
                        st.divider()

    if submitted:
        if not uploaded_files:
            st.error("Please select at least one file to import.")
        else:
            # P0: Validate upload sizes
            size_err = validate_upload_sizes(uploaded_files)
            if size_err:
                st.error(size_err)
            else:
                strategy = strategy_choice
                if strategy == "fixed" and window_size:
                    strategy = f"fixed:{window_size}"

                from polyphony.db.connection import connect
                from polyphony.io.importers import import_documents

                conn = connect(Path(db_path))

                temp_paths = []
                with tempfile.TemporaryDirectory() as tmpdir:
                    for uf in uploaded_files:
                        dest = Path(tmpdir) / uf.name
                        dest.write_bytes(uf.read())
                        temp_paths.append(dest)

                    progress_bar = st.progress(0, text="Importing files…")
                    try:
                        result = import_documents(
                            conn=conn,
                            project_id=project_id,
                            paths=temp_paths,
                            segment_strategy=strategy,
                            content_col=content_col,
                            project_dir=project_dir,
                        )
                        conn.commit()
                        progress_bar.progress(100, text="Done!")

                        total_docs = result.get("total_docs", 0)
                        total_segs = result.get("total_segments", 0)
                        skipped = result.get("skipped", [])

                        st.success(
                            f"Imported **{total_docs}** document(s) → **{total_segs}** segments."
                        )
                        if skipped:
                            st.warning(f"Skipped {len(skipped)} file(s): {', '.join(skipped)}")

                        update_project_status(db_path, project_id, "importing")
                        st.rerun()
                    except Exception as e:
                        st.error(safe_error_message(e, "Import"))
                    finally:
                        conn.close()

    with st.expander("ℹ️ Tips for importing data"):
        st.markdown("""
**Interview transcripts (.txt):**
Use paragraph segmentation. Each paragraph becomes a segment.

**Survey responses (.csv):**
Make sure the column containing responses is named `content` (or specify its name above).
Use sentence or paragraph segmentation.

**Word documents (.docx):**
Polyphony extracts all text from the document. Use paragraph segmentation.

**Images:**
Each image is treated as one segment. The AI will analyze the visual content
using a vision-capable model (GPT-4o, Claude, LLaVA, etc.).

**Large corpora:**
If you have many files, import them in batches. Polyphony deduplicates by content hash,
so re-importing the same file is safe.

**How many segments is typical?**
Most qualitative studies work with 50–500 segments. Fewer than 20 may not be enough
for reliable coding; more than 1,000 will take longer and cost more with cloud models.
""")


# ── Audio / Transcription tab ────────────────────────────────────────────────
with tab_audio:
    st.markdown("### Transcribe Audio Files")
    st.markdown(
        "Upload audio files (MP3, WAV, M4A, etc.) and Polyphony will transcribe them "
        "using local Whisper or the OpenAI Whisper API. Optional: enable speaker "
        "diarization to preserve who said what."
    )

    with st.form("audio_form"):
        audio_files = st.file_uploader(
            "Upload audio files",
            accept_multiple_files=True,
            type=["mp3", "wav", "m4a", "ogg", "flac", "webm", "mp4"],
            help="Supported: MP3, WAV, M4A, OGG, FLAC, WebM, MP4",
        )

        col_prov, col_model = st.columns(2)
        with col_prov:
            transcription_provider = st.selectbox(
                "Transcription engine",
                options=["local_whisper", "openai_whisper"],
                format_func=lambda x: {
                    "local_whisper": "🖥️ Local Whisper (free, private — requires faster-whisper)",
                    "openai_whisper": "☁️ OpenAI Whisper API (fast, requires API key)",
                }[x],
                help=(
                    "**Local Whisper** runs on your machine using faster-whisper. "
                    "No data leaves your computer. Requires `pip install polyphony[audio]`.\n\n"
                    "**OpenAI Whisper** sends audio to OpenAI's API. "
                    "Faster for large files but requires an API key and internet."
                ),
            )
        with col_model:
            whisper_model = st.selectbox(
                "Whisper model size",
                options=["tiny", "base", "small", "medium", "large-v3"],
                index=2,
                help=(
                    "Larger models produce better transcriptions but are slower.\n"
                    "- **tiny/base**: fast, lower accuracy\n"
                    "- **small**: good balance (recommended)\n"
                    "- **medium/large-v3**: best quality, requires more RAM/GPU"
                ),
            )

        language_hint = st.text_input(
            "Language hint (optional)",
            value="en",
            help="ISO 639-1 code (e.g., 'en', 'es', 'fr'). Helps Whisper choose the right model.",
        )

        enable_diarization = st.checkbox(
            "Enable speaker diarization",
            value=False,
            help=(
                "Identify who is speaking in multi-speaker audio (interviews, focus groups). "
                "Requires `pip install polyphony[diarize]` and a HuggingFace token for pyannote."
            ),
        )
        num_speakers = None
        if enable_diarization:
            num_speakers = st.number_input(
                "Expected number of speakers",
                min_value=2, max_value=20, value=2,
                help="Approximate number of distinct speakers in the audio.",
            )

        seg_strategy_audio = st.selectbox(
            "Segmentation after transcription",
            options=["speaker_turn", "paragraph", "sentence"],
            format_func=lambda x: {
                "speaker_turn": "Speaker turn (requires diarization)",
                "paragraph": "Paragraph",
                "sentence": "Sentence",
            }[x],
            index=0 if enable_diarization else 1,
            help="How to split the transcript into codeable segments.",
        )

        transcribe_btn = st.form_submit_button("Transcribe & Import", type="primary")

    if transcribe_btn:
        if not audio_files:
            st.error("Please upload at least one audio file.")
        else:
            size_err = validate_upload_sizes(audio_files)
            if size_err:
                st.error(size_err)
            else:
                try:
                    from polyphony.io.transcribers import transcribe_audio_file
                    from polyphony.io.importers import import_documents
                    from polyphony.db.connection import connect

                    conn = connect(Path(db_path))
                    audio_dir = project_dir / "audio"
                    audio_dir.mkdir(exist_ok=True)
                    all_transcript_paths = []

                    for i, af in enumerate(audio_files):
                        progress = st.progress(
                            int((i / len(audio_files)) * 100),
                            text=f"Transcribing '{af.name}'… ({i+1}/{len(audio_files)})"
                        )
                        with tempfile.NamedTemporaryFile(
                            suffix=Path(af.name).suffix, delete=False
                        ) as tmp:
                            tmp.write(af.read())
                            tmp_path = Path(tmp.name)

                        try:
                            result = transcribe_audio_file(
                                source_path=tmp_path,
                                project_audio_dir=audio_dir,
                                provider=transcription_provider,
                                model=whisper_model if transcription_provider == "local_whisper" else None,
                                language=language_hint or None,
                                diarize=enable_diarization,
                                num_speakers=num_speakers,
                            )
                            transcript_path = project_dir / "transcripts" / f"{Path(af.name).stem}.txt"
                            transcript_path.parent.mkdir(exist_ok=True)
                            transcript_path.write_text(result["text"], encoding="utf-8")
                            all_transcript_paths.append(transcript_path)
                            st.write(f"✅ Transcribed '{af.name}' — {len(result['text'].split())} words")
                        except Exception as e:
                            st.warning(safe_error_message(e, f"Transcription of '{af.name}'"))
                        finally:
                            tmp_path.unlink(missing_ok=True)

                    if all_transcript_paths:
                        seg_strat = seg_strategy_audio
                        if seg_strat == "speaker_turn" and not enable_diarization:
                            seg_strat = "paragraph"

                        progress.progress(90, text="Importing transcripts…")
                        result = import_documents(
                            conn=conn,
                            project_id=project_id,
                            paths=all_transcript_paths,
                            segment_strategy=seg_strat,
                            project_dir=project_dir,
                        )
                        conn.commit()
                        progress.progress(100, text="Done!")
                        st.success(
                            f"Transcribed and imported **{result.get('total_docs', 0)}** file(s) "
                            f"→ **{result.get('total_segments', 0)}** segments."
                        )
                        update_project_status(db_path, project_id, "importing")
                        st.rerun()

                    conn.close()
                except ImportError:
                    st.error(
                        "Transcription requires additional packages. Install with:\n\n"
                        "```\npip install polyphony[audio]\n```\n\n"
                        "For speaker diarization:\n\n"
                        "```\npip install polyphony[diarize]\n```"
                    )
                except Exception as e:
                    st.error(safe_error_message(e, "Audio transcription"))


# ── Podcast tab ───────────────────────────────────────────────────────────────
with tab_podcast:
    st.markdown("### Import Podcast Episodes")
    st.markdown(
        "Enter a podcast RSS feed URL to preview episodes, select which ones to "
        "download, transcribe, and import into your project."
    )

    feed_url = st.text_input(
        "Podcast RSS Feed URL",
        placeholder="https://feeds.example.com/podcast.xml",
        help="Find a podcast's RSS feed URL in its show notes or on sites like podcastindex.org",
    )

    if feed_url and st.button("Preview Feed", key="preview_podcast"):
        try:
            from polyphony.io.podcast import preview_podcast_feed

            with st.spinner("Fetching feed…"):
                preview = preview_podcast_feed(feed_url, limit=25)

            st.session_state["podcast_preview"] = preview
            st.session_state["podcast_feed_url"] = feed_url
        except ImportError:
            st.error("Podcast support requires: `pip install polyphony[audio]`")
        except Exception as e:
            st.error(safe_error_message(e, "Feed preview"))

    if "podcast_preview" in st.session_state:
        preview = st.session_state["podcast_preview"]
        st.markdown(f"### {preview.get('feed_title', 'Podcast Feed')}")

        episodes = preview.get("episodes", [])
        estimate = preview.get("download_estimate", {})

        if estimate:
            col1, col2, col3 = st.columns(3)
            col1.metric("Episodes found", estimate.get("episode_count", len(episodes)))
            total_mb = (estimate.get("total_bytes", 0) or 0) / (1024 * 1024)
            col2.metric("Est. download", f"{total_mb:.0f} MB")
            total_dur = estimate.get("total_duration", 0) or 0
            col3.metric("Est. duration", f"{total_dur // 60} min")

        if episodes:
            import pandas as pd
            ep_df = pd.DataFrame([{
                "Select": True,
                "#": i + 1,
                "Title": ep.get("title", "?")[:60],
                "Duration": ep.get("duration_display", "?"),
                "Date": (ep.get("published", "") or "")[:10],
                "Size (MB)": f"{(ep.get('enclosure_length', 0) or 0) / (1024*1024):.1f}",
            } for i, ep in enumerate(episodes[:25])])

            edited_df = st.data_editor(
                ep_df, use_container_width=True, hide_index=True,
                column_config={"Select": st.column_config.CheckboxColumn("Select")},
            )
            selected_indices = [i for i, row in edited_df.iterrows() if row.get("Select")]

            col_provider, col_diarize = st.columns(2)
            with col_provider:
                pod_provider = st.selectbox(
                    "Transcription engine",
                    options=["local_whisper", "openai_whisper"],
                    format_func=lambda x: "🖥️ Local Whisper" if x == "local_whisper" else "☁️ OpenAI API",
                    key="podcast_trans_provider",
                )
            with col_diarize:
                pod_diarize = st.checkbox("Enable speaker diarization", key="podcast_diarize")
                pod_speakers = None
                if pod_diarize:
                    pod_speakers = st.number_input(
                        "Speakers", min_value=2, max_value=20, value=2,
                        key="podcast_speakers",
                    )

            if st.button("Download, Transcribe & Import", type="primary", key="podcast_import"):
                if not selected_indices:
                    st.error("Please select at least one episode.")
                else:
                    selected_eps = [episodes[i] for i in selected_indices if i < len(episodes)]
                    try:
                        from polyphony.io.podcast import download_podcast_episodes
                        from polyphony.io.transcribers import transcribe_audio_file
                        from polyphony.io.importers import import_documents
                        from polyphony.db.connection import connect

                        conn = connect(Path(db_path))
                        audio_dir = project_dir / "audio" / "podcasts"
                        audio_dir.mkdir(parents=True, exist_ok=True)

                        with st.spinner("Downloading episodes…"):
                            downloads = download_podcast_episodes(selected_eps, audio_dir)

                        all_transcript_paths = []
                        for j, dl in enumerate(downloads):
                            if dl.get("error"):
                                st.warning(f"Skipped: {dl.get('title', '?')} — {dl['error']}")
                                continue
                            audio_path = Path(dl["audio_path"])
                            prog = st.progress(
                                int((j / len(downloads)) * 100),
                                text=f"Transcribing '{dl.get('title', audio_path.name)}'…"
                            )
                            try:
                                result = transcribe_audio_file(
                                    source_path=audio_path,
                                    project_audio_dir=audio_dir,
                                    provider=pod_provider,
                                    diarize=pod_diarize,
                                    num_speakers=pod_speakers,
                                )
                                transcript_path = (
                                    project_dir / "transcripts" / f"{audio_path.stem}.txt"
                                )
                                transcript_path.parent.mkdir(exist_ok=True)
                                transcript_path.write_text(result["text"], encoding="utf-8")
                                all_transcript_paths.append(transcript_path)
                            except Exception as e:
                                st.warning(
                                    safe_error_message(e, f"Transcription of '{dl.get('title')}'")
                                )

                        if all_transcript_paths:
                            seg_strat = "speaker_turn" if pod_diarize else "paragraph"
                            result = import_documents(
                                conn=conn,
                                project_id=project_id,
                                paths=all_transcript_paths,
                                segment_strategy=seg_strat,
                                project_dir=project_dir,
                            )
                            conn.commit()
                            st.success(
                                f"Imported **{result.get('total_docs', 0)}** episode(s) "
                                f"→ **{result.get('total_segments', 0)}** segments."
                            )
                            update_project_status(db_path, project_id, "importing")
                            st.rerun()
                        conn.close()

                    except ImportError:
                        st.error("Podcast import requires: `pip install polyphony[audio]`")
                    except Exception as e:
                        st.error(safe_error_message(e, "Podcast import"))


# ── RSS Feed tab ──────────────────────────────────────────────────────────────
with tab_rss:
    st.markdown("### Import from RSS / Atom Feed")
    st.markdown(
        "Import text articles from an RSS or Atom feed (news sites, blogs, academic feeds). "
        "Each entry becomes a document in your corpus."
    )

    rss_url = st.text_input(
        "RSS/Atom Feed URL",
        placeholder="https://feeds.example.com/news.xml",
        key="rss_url",
    )

    col_kw, col_days = st.columns(2)
    with col_kw:
        rss_keywords = st.text_input(
            "Keyword filter (optional)",
            help="Comma-separated keywords. Only entries containing these words will be shown.",
            key="rss_keywords",
        )
    with col_days:
        rss_since = st.number_input(
            "Only entries from last N days",
            min_value=0, max_value=365, value=0,
            help="0 = no date filter",
            key="rss_since",
        )

    if rss_url and st.button("Preview Feed", key="preview_rss"):
        try:
            from polyphony.io.rss import fetch_rss_entries

            keywords = (
                [k.strip() for k in rss_keywords.split(",") if k.strip()]
                if rss_keywords else None
            )
            with st.spinner("Fetching feed…"):
                result = fetch_rss_entries(
                    rss_url,
                    limit=50,
                    keywords=keywords,
                    since_days=rss_since if rss_since > 0 else None,
                )
            st.session_state["rss_preview"] = result
        except Exception as e:
            st.error(safe_error_message(e, "RSS feed"))

    if "rss_preview" in st.session_state:
        result = st.session_state["rss_preview"]
        entries = result.get("entries", [])
        st.markdown(f"**{result.get('feed_title', 'Feed')}** — {len(entries)} entries")

        if entries:
            import pandas as pd
            ent_df = pd.DataFrame([{
                "Select": True,
                "#": i + 1,
                "Title": e.get("title", "?")[:80],
                "Date": (e.get("published", "") or "")[:10],
                "Words": len((e.get("content") or e.get("summary") or "").split()),
            } for i, e in enumerate(entries)])

            edited_ent = st.data_editor(
                ent_df, use_container_width=True, hide_index=True,
                column_config={"Select": st.column_config.CheckboxColumn("Select")},
            )
            selected_rss = [i for i, row in edited_ent.iterrows() if row.get("Select")]

            rss_seg_strategy = st.selectbox(
                "Segmentation", options=["paragraph", "sentence", "manual"],
                key="rss_seg_strategy",
            )

            if st.button("Import Selected Entries", type="primary", key="rss_import"):
                if not selected_rss:
                    st.error("Please select at least one entry.")
                else:
                    try:
                        from polyphony.db.connection import connect
                        from polyphony.io.importers import import_documents

                        conn = connect(Path(db_path))
                        text_paths = []

                        with tempfile.TemporaryDirectory() as tmpdir:
                            for idx in selected_rss:
                                if idx >= len(entries):
                                    continue
                                entry = entries[idx]
                                title = entry.get("title", f"entry_{idx}")
                                content = entry.get("content") or entry.get("summary") or ""
                                safe_name = "".join(
                                    c if c.isalnum() or c in " -_" else "_" for c in title
                                )[:80]
                                path = Path(tmpdir) / f"{safe_name}.txt"
                                path.write_text(content, encoding="utf-8")
                                text_paths.append(path)

                            result = import_documents(
                                conn=conn,
                                project_id=project_id,
                                paths=text_paths,
                                segment_strategy=rss_seg_strategy,
                                project_dir=project_dir,
                            )
                            conn.commit()

                        st.success(
                            f"Imported **{result.get('total_docs', 0)}** entries "
                            f"→ **{result.get('total_segments', 0)}** segments."
                        )
                        update_project_status(db_path, project_id, "importing")
                        conn.close()
                        st.rerun()
                    except Exception as e:
                        st.error(safe_error_message(e, "RSS import"))
