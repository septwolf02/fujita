# VS — Modules 1–5

This local app includes:

- **Module 1:** convert a Japanese script into editable Content Blocks. Japanese
  is the source of truth; `VN_CHECK` is a close Vietnamese meaning check.
- **Module 2:** plan Visual Scenes separately from script blocks, edit scene
  metadata and image prompts, and maintain a Character Bible and Period Kits.
- **Module 3:** generate versioned images through a provider-neutral engine,
  keep them inside the active project, and select the image used by each scene.
- **Module 4:** generate versioned Japanese WAV audio for each Content Block,
  preview it in the browser, and select the active audio version.
- **Module 5:** build an audio-clock timeline and render versioned local MP4
  files with FFmpeg.

The included fake image and TTS providers create local placeholder PNG and
valid WAV files for a complete offline workflow. YouTube upload is not
implemented.

## Requirements

- Python 3.10 or newer
- A current web browser
- FFmpeg for MP4 rendering; timeline editing and FFmpeg dry runs work without it

There are no third-party packages to install.

## Start on Windows

Double-click `run.bat`. The app opens at <http://127.0.0.1:8000>.

Alternatively, open Command Prompt in this folder and run:

```bat
py -3 app.py --open
```

To keep projects on another drive, pass the workspace directory to `run.bat`:

```bat
run.bat "D:\VS Workspace"
```

The equivalent environment setting is:

```bat
set VS_WORKSPACE_ROOT=D:\VS Workspace
run.bat
```

## Start on macOS or Linux

```sh
python3 app.py --workspace-root "/path/to/factory-data"
```

Open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

## Module 1 workflow

1. Paste a Japanese script.
2. Split it by sentences or blank-line paragraphs.
3. Review and edit `JP` and enter a close translation in `VN_CHECK`.
4. Add or delete blocks where needed, then choose **Lưu thay đổi**.

## Module 2 workflow

1. Open **Visual Scenes** and choose **Tạo từ blocks** for an initial one-to-one
   plan, or add scenes manually.
2. Select any number of source blocks for each scene and edit all scene
   metadata, including `image_prompt`.
3. Select consecutive scenes and choose **Merge Scene** to combine their block
   coverage. Choose **Split Scene** on one card to divide its block coverage.
   When a scene covers one block, splitting deliberately creates two visuals
   linked to that same block.
4. Use **Character Bible** to maintain stable face/body profiles. Use **Period
   Kits** for historical costume, hair, accessories, footwear, and setting
   guidance. A kit can be applied to an individual scene.

## Module 3 workflow

1. Enter an `image_prompt` and scene metadata.
2. Choose **Generate 1**, **Generate Variations**, or **Regenerate**. Regenerate
   always creates the next version and retains prior files.
3. Choose one gallery image as `selected_image`. Files are removed only through
   the explicit **Xóa** action.

Images are named from the scene and version, such as `VS001_01.png`, and stored
under the active project's `images/` directory. The generation request combines
the scene prompt, character profiles and available reference files, period kit,
costume, hair, action, emotion, and shot metadata.

The default provider setting lives in `workspace_root/config.json`:

```json
{
  "image_generation": {
    "provider": "fake",
    "aspect_ratio": "16:9",
    "quality": "standard"
  }
}
```

Provider adapters implement `ImageProvider.generate_image(request)` and are
registered with `ProviderRegistry`. A future real adapter should read API keys
from environment variables or another runtime secret source; credentials are
never stored in source code, projects, or scene JSON.

## Module 4 workflow

1. Save the Japanese text in a Content Block.
2. Choose **Generate 1** on that block, or **Generate All Audio** for every
   block in the active project.
3. Use **Regenerate** to create the next version without removing older WAV
   files. Choose any prior version to make it the block's `audio_file`.
4. Preview the selected version with the audio player on the block card.

Audio belongs to Content Blocks, independently of Visual Scenes. Files use
relative versioned paths such as `audio/S001_01.wav`; the block keeps all paths
in `audio_assets`, the active path in `audio_file`, and WAV length in
`duration`.

TTS configuration lives in `workspace_root/config.json`. `fake` remains the
offline default; set `provider` to `voicevox` to use the local engine:

```json
{
  "tts": {
    "provider": "voicevox",
    "voicevox": {
      "engine_url": "http://127.0.0.1:50021",
      "speaker_name": "剣崎雌雄",
      "style_name": "ノーマル",
      "required_credit": "VOICEVOX:剣崎雌雄"
    }
  }
}
```

Provider adapters implement `TTSProvider.generate_voice(request)` and are
registered with `TTSProviderRegistry`. Real adapters should read credentials
from environment variables or another runtime secret source.

The VOICEVOX adapter checks `/version`, resolves the style ID from `/speakers`,
posts Japanese text to `/audio_query`, and sends the returned query to
`/synthesis`. The resulting WAV is validated and measured before the Content
Block is updated. A connection or speaker error leaves existing audio intact.

After a successful generation, the configured `required_credit` is copied into
the active project's `project.json`. Repeated generations with the same voice
do not duplicate it. Credits from older providers or voices remain available
for outputs that still use their audio. Future publishing code can read the
normalized list with `WorkspaceStorage.load_project_credits()`.

### Test VOICEVOX on Windows

1. Start your local VOICEVOX application and keep it running.
2. Verify the local engine from PowerShell:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:50021/version
   Invoke-RestMethod http://127.0.0.1:50021/speakers
   ```

3. Update the active workspace's `config.json` with the `tts` configuration
   above and restart this app.
4. Open **Content Blocks** and choose **Generate 1** or **Generate All Audio**.
   The selected WAV appears under `projects/<project_id>/audio/` and can be
   previewed in the same block card.

To return to offline test audio, set `"provider": "fake"`. The VOICEVOX
installation directory is not stored by this app because synthesis uses the
local HTTP engine URL.

## Module 5 workflow

1. Generate or select audio for every Content Block and select an image for
   every Visual Scene.
2. Open **Timeline & Render**. Scene durations are calculated from real audio
   duration. For several auto scenes covering one block, time is divided by
   `duration_weight`; equal weights split it evenly.
3. Change scene order, motion, transition, weight, or switch a scene to manual
   mode with `duration_override`. Choose **Lưu & Rebuild**.
4. Use **FFmpeg Dry Run** to inspect the command, then **Render MP4**. Outputs
   use versioned names such as `output/draft_001.mp4`; prior renders remain.

Manual scene time is reserved first. Remaining time in each source block is
distributed across auto scenes, so visuals adapt to the audio master clock.
Missing audio, duration, selected images, invalid order, and timing gaps are
reported before FFmpeg runs.

Renderer configuration is stored in `workspace_root/config.json`:

```json
{
  "video_render": {
    "renderer": "ffmpeg",
    "ffmpeg_path": "ffmpeg",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "video_codec": "libx264",
    "audio_codec": "aac"
  }
}
```

Set `ffmpeg_path` to an executable name available on `PATH` or an explicit
local path. `VS_FFMPEG_PATH` supplies the initial value for a new workspace.

## Local workspace

If no path is supplied, the app uses `workspace_data` beside the application.
The location can be changed with `--workspace-root` or the
`VS_WORKSPACE_ROOT` environment variable. All filesystem work uses
`pathlib`, so the same code supports Windows and cloud/Linux paths.

The legacy `FUJITA_WORKSPACE_ROOT` and `FUJITA_FFMPEG_PATH` names are still
accepted so existing installations continue to start without migration.

## Package VS

Run this command from the application directory:

```bat
py -3 package_vs.py
```

It creates `VS.zip`. The archive always has one top-level directory named
`VS/`; after extraction, the main application directory is therefore `VS`.
Workspace data, caches, tests, and existing ZIP files are excluded.

```text
workspace_root/
  config.json
  library/
    characters/<CHAR_ID>/
      character.json
      face_references/
      expression_references/
    period_kits/<PERIOD_ID>/period_kit.json
    styles/
  projects/<project_id>/
    project.json
    content_blocks.json
    visual_scenes.json
    timeline.json
    images/
    audio/
    thumbnails/
    temp/
    output/
```

The project selector in the header creates videos as separate projects and
switches the active project. Character Bible and Period Kits remain shared by
every project in the workspace.

Visual Scenes store Content Block IDs in `source_blocks`; they never duplicate
the Japanese or Vietnamese script. This supports many blocks per scene and
multiple scenes per block.

Asset paths stored in project JSON are relative to the project directory and
use portable forward slashes, for example `images/VS001_01.png`. Absolute and
parent-traversal paths are rejected.

## Migration from the flat data layout

On first launch, the app checks the former `data/` directory. Existing
`content_blocks.json`, `visual_scenes.json`, `character_bible.json`, and
`period_kits.json` files are copied into the new active project or shared
library when their destination is empty. Missing schema fields are added, and
scene links are rebuilt. Source files are retained. The migration result is
recorded in `workspace_root/config.json`, so it runs only once per workspace.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The application uses only Python's standard library. `module1.py` contains the
script rules. `module2.py` owns visual planning, merge/split, Character Bible,
and Period Kit rules. `storage.py` owns workspace, project, shared-library, and
migration behavior. `image_engine.py` handles image requests and scene updates;
`image_providers/` contains its adapters. `tts_engine.py` handles atomic Content
Block audio generation and selection; `tts_providers/` contains its contract
and offline fake provider. `timeline_engine.py` allocates real audio durations;
`video_renderer.py` builds and executes FFmpeg without a shell. `app.py`
provides the local HTTP API, and `static/` contains the browser interface.

