const state = {
  blocks: [], scenes: [], characters: [], periodKits: [],
  timeline: { items: [], warnings: [], total_duration: 0 }, renderer: {},
  projects: [], activeProject: null, workspaceRoot: "",
  imageProviders: [], activeProvider: "fake", imageAspectRatio: "16:9", imageQuality: "standard",
  ttsProviders: [], activeTTSProvider: "fake", ttsVoiceId: "fake-ja", ttsStyle: "neutral", ttsSpeed: 1, ttsEngineUrl: null,
  selectedScenes: new Set(),
  dirty: { blocks: false, scenes: false, characters: false, periods: false },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const elements = {
  script: $("#script-input"), mode: $("#split-mode"), split: $("#split-button"),
  blockList: $("#block-list"), blockEmpty: $("#block-empty"), blockCount: $("#block-count"),
  sceneList: $("#scene-list"), sceneEmpty: $("#scene-empty"), sceneCount: $("#scene-count"),
  characterList: $("#character-list"), characterCount: $("#character-count"),
  periodList: $("#period-list"), periodCount: $("#period-count"), periodOptions: $("#period-options"),
  projectSelect: $("#project-select"), workspaceLocation: $("#workspace-location"),
  providerSummary: $("#provider-summary"),
  ttsProviderSummary: $("#tts-provider-summary"),
  timelineList: $("#timeline-list"), timelineEmpty: $("#timeline-empty"), timelineCount: $("#timeline-count"),
  timelineDuration: $("#timeline-duration"), timelineGenerated: $("#timeline-generated"),
  timelineWarnings: $("#timeline-warnings"), rendererSummary: $("#renderer-summary"),
  renderResult: $("#render-result"),
  toast: $("#toast"),
};

function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${isError ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { elements.toast.className = "toast"; }, 3000);
}

function setDirty(area, dirty) {
  state.dirty[area] = dirty;
  const status = $(`#${area === "periods" ? "period" : area.slice(0, -1)}-status`);
  if (status) status.textContent = dirty ? "Có thay đổi chưa lưu" : "Đã lưu";
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function nextNumericId(records, field, prefix) {
  const maximum = records.reduce((max, record) => {
    const number = Number(record[field].slice(prefix.length));
    return Number.isFinite(number) ? Math.max(max, number) : max;
  }, 0);
  return `${prefix}${String(maximum + 1).padStart(3, "0")}`;
}

function bindText(root, selector, object, field, area) {
  const input = $(selector, root);
  input.value = object[field] ?? "";
  input.addEventListener("input", () => { object[field] = input.value; setDirty(area, true); });
}

function renderBlocks() {
  elements.blockList.replaceChildren();
  elements.blockCount.textContent = state.blocks.length;
  elements.blockEmpty.hidden = state.blocks.length > 0;
  state.blocks.forEach((block, index) => {
    const fragment = $("#block-template").content.cloneNode(true);
    $(".block-id", fragment).textContent = block.block_id;
    bindText(fragment, ".jp-text", block, "jp_text", "blocks");
    bindText(fragment, ".vn-check", block, "vn_check", "blocks");
    const audio = $(".audio-preview", fragment);
    const duration = Number(block.duration);
    $(".audio-duration", fragment).textContent = Number.isFinite(duration) && duration > 0 ? `${duration.toFixed(2)} s` : "Chưa có audio";
    $(".audio-file", fragment).value = block.audio_file || "";
    if (block.audio_file) audio.src = assetUrl(block.audio_file);
    else audio.hidden = true;
    const assets = $(".audio-assets", fragment);
    (block.audio_assets || []).forEach((relativePath) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "audio-version-button";
      button.textContent = `${relativePath.split("/").pop()}${block.audio_file === relativePath ? " ✓" : ""}`;
      button.disabled = block.audio_file === relativePath;
      button.addEventListener("click", () => selectBlockAudio(block.block_id, relativePath));
      assets.append(button);
    });
    $(".generate-audio-button", fragment).addEventListener("click", (event) => generateBlockAudio(block.block_id, "generate", event.currentTarget));
    $(".regenerate-audio-button", fragment).addEventListener("click", (event) => generateBlockAudio(block.block_id, "regenerate", event.currentTarget));
    $(".delete-button", fragment).addEventListener("click", () => {
      state.blocks.splice(index, 1); setDirty("blocks", true); renderBlocks();
    });
    elements.blockList.append(fragment);
  });
}

function updatePeriodOptions() {
  elements.periodOptions.replaceChildren(...state.periodKits.map((kit) => {
    const option = document.createElement("option"); option.value = kit.name; option.label = kit.period_id; return option;
  }));
}

function sourceSummary(sourceIds) {
  return sourceIds.map((id) => state.blocks.find((block) => block.block_id === id)).filter(Boolean);
}

function renderWorkspace() {
  elements.projectSelect.replaceChildren(...state.projects.map((project) => {
    const option = document.createElement("option");
    option.value = project.project_id; option.textContent = project.title;
    option.selected = project.project_id === state.activeProject?.project_id;
    return option;
  }));
  elements.workspaceLocation.textContent = state.workspaceRoot;
  elements.workspaceLocation.title = state.workspaceRoot;
  elements.providerSummary.textContent = `Provider: ${state.activeProvider} · ${state.imageAspectRatio} · ${state.imageQuality}`;
  elements.ttsProviderSummary.textContent = `TTS: ${state.activeTTSProvider} · ${state.ttsVoiceId} / ${state.ttsStyle} · ${state.ttsSpeed}×${state.ttsEngineUrl ? ` · ${state.ttsEngineUrl}` : ""}`;
  elements.rendererSummary.textContent = `FFmpeg: ${state.renderer.available ? "ready" : "not found"} · ${state.renderer.width || 1920}×${state.renderer.height || 1080} · ${state.renderer.fps || 30}fps`;
}

function assetUrl(relativePath) {
  return `/project-assets/${relativePath.split("/").map(encodeURIComponent).join("/")}`;
}

function renderScenes() {
  elements.sceneList.replaceChildren();
  elements.sceneCount.textContent = state.scenes.length;
  elements.sceneEmpty.hidden = state.scenes.length > 0;
  updatePeriodOptions();
  state.scenes.forEach((scene, index) => {
    const fragment = $("#scene-template").content.cloneNode(true);
    const card = $(".scene-card", fragment);
    $(".scene-id", fragment).textContent = scene.scene_id;
    const selected = $(".scene-selected", fragment);
    selected.checked = state.selectedScenes.has(scene.scene_id);
    selected.addEventListener("change", () => selected.checked ? state.selectedScenes.add(scene.scene_id) : state.selectedScenes.delete(scene.scene_id));

    const sourceSelect = $(".source-blocks", fragment);
    state.blocks.forEach((block) => {
      const option = document.createElement("option");
      option.value = block.block_id;
      option.textContent = `${block.block_id} — ${block.jp_text.slice(0, 48)}`;
      option.selected = scene.source_blocks.includes(block.block_id);
      sourceSelect.append(option);
    });
    const refreshSourceDisplay = () => {
      const summaries = sourceSummary(scene.source_blocks);
      $(".coverage", card).textContent = scene.source_blocks.join(" · ");
      const preview = $(".source-preview", card); preview.replaceChildren();
      summaries.forEach((block) => {
        const item = document.createElement("p");
        item.textContent = `${block.block_id}  ${block.jp_text}`; preview.append(item);
      });
    };
    sourceSelect.addEventListener("change", () => {
      scene.source_blocks = [...sourceSelect.selectedOptions].map((option) => option.value);
      setDirty("scenes", true); refreshSourceDisplay();
    });
    refreshSourceDisplay();

    const characterSelect = $(".scene-characters", fragment);
    state.characters.forEach((character) => {
      const option = document.createElement("option"); option.value = character.character_id;
      option.textContent = `${character.character_id} — ${character.name}`;
      option.selected = scene.characters.includes(character.character_id); characterSelect.append(option);
    });
    characterSelect.addEventListener("change", () => {
      scene.characters = [...characterSelect.selectedOptions].map((option) => option.value); setDirty("scenes", true);
    });

    const fields = {
      ".period": "period", ".year": "year", ".location": "location",
      ".costume-profile": "costume_profile", ".hair-profile": "hair_profile",
      ".action": "action", ".emotion": "emotion", ".shot-type": "shot_type",
      ".scene-note": "scene_note", ".image-prompt": "image_prompt",
    };
    Object.entries(fields).forEach(([selector, field]) => bindText(fragment, selector, scene, field, "scenes"));
    const imageAssets = $(".image-assets", fragment);
    imageAssets.value = (scene.image_assets || []).join("\n");
    const selectedImage = $(".selected-image", fragment);
    selectedImage.value = scene.selected_image || "";
    selectedImage.readOnly = true;

    const gallery = $(".asset-gallery", fragment);
    (scene.image_assets || []).forEach((relativePath) => {
      const item = document.createElement("figure"); item.className = `asset-item${scene.selected_image === relativePath ? " selected" : ""}`;
      const image = document.createElement("img"); image.src = assetUrl(relativePath); image.alt = `${scene.scene_id} ${relativePath}`; image.loading = "lazy";
      const caption = document.createElement("figcaption");
      const name = document.createElement("span"); name.textContent = relativePath.split("/").pop();
      const selectButton = document.createElement("button"); selectButton.type = "button"; selectButton.className = "asset-select-button";
      selectButton.textContent = scene.selected_image === relativePath ? "Đã chọn" : "Chọn ảnh";
      selectButton.addEventListener("click", () => selectSceneImage(scene.scene_id, relativePath));
      const deleteButton = document.createElement("button"); deleteButton.type = "button"; deleteButton.className = "asset-delete-button"; deleteButton.textContent = "Xóa";
      deleteButton.addEventListener("click", () => deleteSceneImage(scene.scene_id, relativePath));
      caption.append(name, selectButton, deleteButton); item.append(image, caption); gallery.append(item);
    });
    $(".generate-one-button", fragment).addEventListener("click", (event) => generateSceneImage(scene.scene_id, "generate", event.currentTarget));
    $(".variations-button", fragment).addEventListener("click", (event) => generateSceneImage(scene.scene_id, "variations", event.currentTarget));
    $(".regenerate-button", fragment).addEventListener("click", (event) => generateSceneImage(scene.scene_id, "regenerate", event.currentTarget));

    $(".apply-kit-button", fragment).addEventListener("click", () => {
      const key = scene.period.trim().toLowerCase();
      const kit = state.periodKits.find((item) => item.name.toLowerCase() === key || item.period_id.toLowerCase() === key);
      if (!kit) return showToast("Không tìm thấy Period Kit khớp với trường Period.", true);
      scene.period = kit.name; scene.year = scene.year || kit.year_range;
      scene.costume_profile = kit.costume_profile; scene.hair_profile = kit.hair_profile;
      setDirty("scenes", true); renderScenes(); showToast(`Đã áp dụng ${kit.name} cho ${scene.scene_id}.`);
    });
    $(".split-scene-button", fragment).addEventListener("click", () => splitScene(scene.scene_id));
    $(".delete-button", fragment).addEventListener("click", () => {
      state.scenes.splice(index, 1); state.selectedScenes.delete(scene.scene_id); setDirty("scenes", true); renderScenes();
    });
    elements.sceneList.append(fragment);
  });
}

function renderTimeline() {
  const timeline = state.timeline || { items: [], warnings: [], total_duration: 0 };
  elements.timelineList.replaceChildren();
  elements.timelineCount.textContent = timeline.items.length;
  elements.timelineEmpty.hidden = timeline.items.length > 0;
  elements.timelineDuration.textContent = `${Number(timeline.total_duration || 0).toFixed(3)}s`;
  elements.timelineGenerated.textContent = timeline.generated_at ? `Generated: ${timeline.generated_at}` : "";
  elements.timelineWarnings.replaceChildren();
  elements.timelineWarnings.hidden = !timeline.warnings.length;
  if (timeline.warnings.length) {
    const title = document.createElement("strong"); title.textContent = `${timeline.warnings.length} cảnh báo cần xử lý`;
    const list = document.createElement("ul");
    timeline.warnings.forEach((warning) => { const item = document.createElement("li"); item.textContent = `[${warning.code}] ${warning.message}`; list.append(item); });
    elements.timelineWarnings.append(title, list);
  }
  timeline.items.forEach((item, index) => {
    const scene = state.scenes.find((candidate) => candidate.scene_id === item.scene_id);
    if (!scene) return;
    const fragment = $("#timeline-template").content.cloneNode(true);
    $(".timeline-scene-id", fragment).textContent = item.scene_id;
    $(".timeline-time", fragment).textContent = `${Number(item.start).toFixed(3)}s → ${Number(item.end).toFixed(3)}s`;
    $(".timeline-duration-item", fragment).textContent = `${Number(item.duration).toFixed(3)}s`;
    $(".timeline-sources", fragment).textContent = item.source_blocks.join(" · ");
    const audioLabels = item.audio_blocks.map((id) => {
      const block = state.blocks.find((candidate) => candidate.block_id === id);
      return block?.audio_file ? `${id}: ${block.audio_file}` : `${id}: missing`;
    });
    $(".timeline-audio", fragment).textContent = audioLabels.join(" · ");
    const image = $(".timeline-image", fragment);
    if (item.selected_image) image.src = assetUrl(item.selected_image); else image.hidden = true;
    $(".timeline-image-path", fragment).textContent = item.selected_image || "Missing selected image";
    const motion = $(".timeline-motion", fragment); motion.value = scene.motion || "hold";
    const transition = $(".timeline-transition", fragment); transition.value = scene.transition || "cut";
    const mode = $(".timeline-mode", fragment); mode.value = scene.duration_mode || "auto";
    const weight = $(".timeline-weight", fragment); weight.value = scene.duration_weight ?? 1;
    const override = $(".timeline-override", fragment); override.value = scene.duration_override ?? "";
    const syncMode = () => { override.disabled = mode.value !== "manual"; weight.disabled = mode.value !== "auto"; };
    syncMode();
    motion.addEventListener("change", () => { scene.motion = motion.value; setDirty("scenes", true); });
    transition.addEventListener("change", () => { scene.transition = transition.value; setDirty("scenes", true); });
    mode.addEventListener("change", () => { scene.duration_mode = mode.value; syncMode(); setDirty("scenes", true); });
    weight.addEventListener("input", () => { scene.duration_weight = Number(weight.value); setDirty("scenes", true); });
    override.addEventListener("input", () => { scene.duration_override = override.value === "" ? null : Number(override.value); setDirty("scenes", true); });
    const warnings = timeline.warnings.filter((warning) => warning.scene_id === item.scene_id || item.source_blocks.includes(warning.block_id));
    const warningBox = $(".item-warnings", fragment);
    warnings.forEach((warning) => { const line = document.createElement("p"); line.textContent = warning.message; warningBox.append(line); });
    const up = $(".move-up", fragment); const down = $(".move-down", fragment);
    up.disabled = index === 0; down.disabled = index === timeline.items.length - 1;
    up.addEventListener("click", () => moveTimelineScene(item.scene_id, -1));
    down.addEventListener("click", () => moveTimelineScene(item.scene_id, 1));
    elements.timelineList.append(fragment);
  });
}

function renderCharacters() {
  elements.characterList.replaceChildren(); elements.characterCount.textContent = state.characters.length;
  state.characters.forEach((character, index) => {
    const fragment = $("#character-template").content.cloneNode(true);
    const mappings = { ".character-id": "character_id", ".character-name": "name", ".character-role": "role", ".face-profile": "face_profile", ".body-profile": "body_profile" };
    Object.entries(mappings).forEach(([selector, field]) => bindText(fragment, selector, character, field, "characters"));
    const refs = $(".reference-images", fragment); refs.value = character.reference_images.join("\n");
    refs.addEventListener("input", () => { character.reference_images = refs.value.split("\n").map((line) => line.trim()).filter(Boolean); setDirty("characters", true); });
    $(".delete-button", fragment).addEventListener("click", () => { state.characters.splice(index, 1); setDirty("characters", true); renderCharacters(); renderScenes(); });
    elements.characterList.append(fragment);
  });
}

function renderPeriods() {
  elements.periodList.replaceChildren(); elements.periodCount.textContent = state.periodKits.length;
  state.periodKits.forEach((kit, index) => {
    const fragment = $("#period-template").content.cloneNode(true);
    const mappings = { ".period-id": "period_id", ".period-name": "name", ".year-range": "year_range", ".kit-costume": "costume_profile", ".kit-hair": "hair_profile", ".kit-accessories": "accessories", ".kit-footwear": "footwear", ".environment-profile": "environment_profile" };
    Object.entries(mappings).forEach(([selector, field]) => bindText(fragment, selector, kit, field, "periods"));
    $(".delete-button", fragment).addEventListener("click", () => { state.periodKits.splice(index, 1); setDirty("periods", true); renderPeriods(); });
    elements.periodList.append(fragment);
  });
  updatePeriodOptions();
}

async function saveBlocks() {
  const body = await api("/api/blocks", { method: "POST", body: JSON.stringify({ blocks: state.blocks }) });
  state.blocks = body.blocks; setDirty("blocks", false); renderBlocks(); return body;
}

async function generateBlockAudio(blockId, operation, button) {
  button.disabled = true;
  try {
    await saveBlocks();
    const body = await api("/api/audio/generate", { method: "POST", body: JSON.stringify({ block_id: blockId, operation }) });
    state.blocks = body.blocks; setDirty("blocks", false); renderBlocks();
    showToast(`Đã tạo ${body.generated} cho ${blockId}.`);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function generateAllAudio() {
  if (!state.blocks.length) return showToast("Cần ít nhất một Content Block.", true);
  const button = $("#generate-all-audio-button"); button.disabled = true;
  try {
    await saveBlocks();
    const body = await api("/api/audio/generate-all", { method: "POST", body: "{}" });
    state.blocks = body.blocks; setDirty("blocks", false); renderBlocks();
    showToast(`Đã tạo ${body.generated.length} file audio.`);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function selectBlockAudio(blockId, path) {
  try {
    if (state.dirty.blocks) await saveBlocks();
    const body = await api("/api/audio/select", { method: "POST", body: JSON.stringify({ block_id: blockId, path }) });
    state.blocks = body.blocks; setDirty("blocks", false); renderBlocks();
    showToast(`Đã chọn audio cho ${blockId}.`);
  } catch (error) { showToast(error.message, true); }
}

async function saveScenes() {
  const body = await api("/api/scenes", { method: "POST", body: JSON.stringify({ scenes: state.scenes }) });
  state.scenes = body.scenes; setDirty("scenes", false); renderScenes(); renderTimeline(); return body;
}

async function refreshTimeline(usePost = false) {
  const body = await api(usePost ? "/api/timeline/regenerate" : "/api/timeline", usePost ? { method: "POST", body: "{}" } : {});
  state.timeline = body.timeline;
  state.timeline.items.forEach((item) => {
    const scene = state.scenes.find((candidate) => candidate.scene_id === item.scene_id);
    if (scene) { scene.timeline_start = item.start; scene.timeline_end = item.end; }
  });
  renderTimeline();
  return body.timeline;
}

async function rebuildTimeline() {
  if (state.dirty.blocks) await saveBlocks();
  if (state.dirty.scenes) await saveScenes();
  return refreshTimeline(true);
}

async function moveTimelineScene(sceneId, direction) {
  const index = state.scenes.findIndex((scene) => scene.scene_id === sceneId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= state.scenes.length) return;
  [state.scenes[index], state.scenes[target]] = [state.scenes[target], state.scenes[index]];
  setDirty("scenes", true);
  try { await rebuildTimeline(); renderScenes(); showToast("Đã cập nhật thứ tự scene."); }
  catch (error) { showToast(error.message, true); }
}

async function runVideoRender(dryRun, button) {
  button.disabled = true;
  elements.renderResult.hidden = true;
  try {
    await rebuildTimeline();
    const result = await api("/api/render", { method: "POST", body: JSON.stringify({ dry_run: dryRun }) });
    elements.renderResult.replaceChildren(); elements.renderResult.hidden = false;
    if (result.status === "completed") {
      const link = document.createElement("a"); link.href = assetUrl(result.output); link.textContent = `Mở ${result.output}`; link.target = "_blank";
      elements.renderResult.append("Render hoàn tất: ", link);
    } else {
      const title = document.createElement("strong"); title.textContent = `Dry run: ${result.output}`;
      const command = document.createElement("pre"); command.textContent = result.command.join(" ");
      elements.renderResult.append(title, command);
    }
    showToast(dryRun ? "FFmpeg command đã sẵn sàng." : "Đã render video MP4.");
  } catch (error) {
    elements.renderResult.textContent = error.message; elements.renderResult.hidden = false;
    showToast(error.message, true);
  } finally { button.disabled = false; }
}

async function splitScene(sceneId) {
  try {
    await saveScenes();
    const body = await api("/api/scenes/split", { method: "POST", body: JSON.stringify({ scene_id: sceneId }) });
    state.scenes = body.scenes; state.selectedScenes.clear(); setDirty("scenes", false); renderScenes(); showToast(`Đã split ${sceneId}.`);
  } catch (error) { showToast(error.message, true); }
}

async function generateSceneImage(sceneId, operation, button) {
  button.disabled = true;
  try {
    await saveScenes();
    const body = await api("/api/images/generate", { method: "POST", body: JSON.stringify({ scene_id: sceneId, operation }) });
    state.scenes = body.scenes; setDirty("scenes", false); renderScenes();
    showToast(`Đã tạo ${body.generated.length} ảnh cho ${sceneId}.`);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function selectSceneImage(sceneId, path) {
  try {
    const body = await api("/api/images/select", { method: "POST", body: JSON.stringify({ scene_id: sceneId, path }) });
    state.scenes = body.scenes; setDirty("scenes", false); renderScenes(); showToast(`Đã chọn ảnh cho ${sceneId}.`);
  } catch (error) { showToast(error.message, true); }
}

async function deleteSceneImage(sceneId, path) {
  if (!window.confirm(`Xóa file ảnh ${path}?`)) return;
  try {
    const body = await api("/api/images/delete", { method: "POST", body: JSON.stringify({ scene_id: sceneId, path }) });
    state.scenes = body.scenes; setDirty("scenes", false); renderScenes(); showToast(`Đã xóa ${path}.`);
  } catch (error) { showToast(error.message, true); }
}

$$('.tab').forEach((tab) => tab.addEventListener("click", async () => {
  $$('.tab').forEach((item) => item.classList.toggle("active", item === tab));
  $$('.view').forEach((view) => { const active = view.id === `view-${tab.dataset.view}`; view.hidden = !active; view.classList.toggle("active", active); });
  if (tab.dataset.view === "timeline" && !state.dirty.blocks && !state.dirty.scenes) {
    try { await refreshTimeline(); } catch (error) { showToast(error.message, true); }
  }
}));

elements.split.addEventListener("click", async () => {
  if (!elements.script.value.trim()) return showToast("Hãy nhập script tiếng Nhật trước.", true);
  if (state.blocks.length && !window.confirm("Danh sách block hiện tại sẽ được thay. Tiếp tục?")) return;
  try {
    const body = await api("/api/split", { method: "POST", body: JSON.stringify({ script: elements.script.value, split_mode: elements.mode.value }) });
    state.blocks = body.blocks; setDirty("blocks", true); renderBlocks(); showToast(`Đã tạo ${state.blocks.length} content block.`);
  } catch (error) { showToast(error.message, true); }
});

$("#add-button").addEventListener("click", () => {
  state.blocks.push({ block_id: nextNumericId(state.blocks, "block_id", "S"), jp_text: "", vn_check: "", scene_ids: [], audio_assets: [], audio_file: null, duration: null }); setDirty("blocks", true); renderBlocks();
  elements.blockList.lastElementChild?.querySelector(".jp-text").focus();
});
$("#save-blocks-button").addEventListener("click", async () => { try { await saveBlocks(); showToast("Đã lưu Content Blocks."); } catch (error) { showToast(error.message, true); } });
$("#generate-all-audio-button").addEventListener("click", generateAllAudio);

$("#create-scenes-button").addEventListener("click", async () => {
  if (!state.blocks.length) return showToast("Cần ít nhất một Content Block.", true);
  if (state.scenes.length && !window.confirm("Visual Scenes hiện tại sẽ được thay bằng một scene cho mỗi block. Tiếp tục?")) return;
  try {
    if (state.dirty.blocks) await saveBlocks();
    const body = await api("/api/scenes/from-blocks", { method: "POST", body: "{}" });
    state.scenes = body.scenes; state.selectedScenes.clear(); setDirty("scenes", false); renderScenes(); showToast(`Đã tạo ${state.scenes.length} Visual Scene.`);
  } catch (error) { showToast(error.message, true); }
});

$("#add-scene-button").addEventListener("click", () => {
  if (!state.blocks.length) return showToast("Cần ít nhất một Content Block.", true);
  state.scenes.push({ scene_id: nextNumericId(state.scenes, "scene_id", "VS"), source_blocks: [state.blocks[0].block_id], period: "", year: "", location: "", characters: [], costume_profile: "", hair_profile: "", action: "", emotion: "", shot_type: "", scene_note: "", image_prompt: "", image_assets: [], selected_image: null });
  setDirty("scenes", true); renderScenes();
});

$("#merge-scenes-button").addEventListener("click", async () => {
  const ids = state.scenes.filter((scene) => state.selectedScenes.has(scene.scene_id)).map((scene) => scene.scene_id);
  if (ids.length < 2) return showToast("Chọn ít nhất hai scene liên tiếp để merge.", true);
  try {
    await saveScenes();
    const body = await api("/api/scenes/merge", { method: "POST", body: JSON.stringify({ scene_ids: ids }) });
    state.scenes = body.scenes; state.selectedScenes.clear(); setDirty("scenes", false); renderScenes(); showToast("Đã merge scene và hợp nhất source_blocks.");
  } catch (error) { showToast(error.message, true); }
});
$("#save-scenes-button").addEventListener("click", async () => { try { await saveScenes(); showToast("Đã lưu Visual Scenes."); } catch (error) { showToast(error.message, true); } });
$("#rebuild-timeline-button").addEventListener("click", async () => { try { await rebuildTimeline(); showToast("Đã rebuild timeline từ audio duration."); } catch (error) { showToast(error.message, true); } });
$("#dry-run-button").addEventListener("click", (event) => runVideoRender(true, event.currentTarget));
$("#render-video-button").addEventListener("click", (event) => runVideoRender(false, event.currentTarget));

$("#add-character-button").addEventListener("click", () => {
  const used = state.characters.map((character) => character.character_id); let number = 1; while (used.includes(`CHAR_${number}`)) number += 1;
  state.characters.push({ character_id: `CHAR_${number}`, name: "", role: "", face_profile: "", body_profile: "", reference_images: [] }); setDirty("characters", true); renderCharacters(); renderScenes();
});
$("#save-characters-button").addEventListener("click", async () => { try { const body = await api("/api/characters", { method: "POST", body: JSON.stringify({ characters: state.characters }) }); state.characters = body.characters; setDirty("characters", false); renderCharacters(); renderScenes(); showToast("Đã lưu Character Bible."); } catch (error) { showToast(error.message, true); } });

$("#add-period-button").addEventListener("click", () => {
  let number = 1; const used = state.periodKits.map((kit) => kit.period_id); while (used.includes(`NEW_PERIOD_${number}`)) number += 1;
  state.periodKits.push({ period_id: `NEW_PERIOD_${number}`, name: "", year_range: "", costume_profile: "", hair_profile: "", accessories: "", footwear: "", environment_profile: "" }); setDirty("periods", true); renderPeriods();
});
$("#save-periods-button").addEventListener("click", async () => { try { const body = await api("/api/period-kits", { method: "POST", body: JSON.stringify({ period_kits: state.periodKits }) }); state.periodKits = body.period_kits; setDirty("periods", false); renderPeriods(); renderScenes(); showToast("Đã lưu Period Kits."); } catch (error) { showToast(error.message, true); } });

window.addEventListener("beforeunload", (event) => { if (Object.values(state.dirty).some(Boolean)) { event.preventDefault(); event.returnValue = ""; } });

async function loadProjectData() {
  const [blocks, scenes, timeline] = await Promise.all([api("/api/blocks"), api("/api/scenes"), api("/api/timeline")]);
  state.blocks = blocks.blocks; state.scenes = scenes.scenes; state.timeline = timeline.timeline; state.selectedScenes.clear();
  setDirty("blocks", false); setDirty("scenes", false); renderBlocks(); renderScenes(); renderTimeline();
}

elements.projectSelect.addEventListener("change", async () => {
  const projectId = elements.projectSelect.value;
  if ((state.dirty.blocks || state.dirty.scenes) && !window.confirm("Thay đổi chưa lưu của project hiện tại sẽ bị bỏ. Tiếp tục?")) {
    renderWorkspace(); return;
  }
  try {
    const body = await api("/api/projects/switch", { method: "POST", body: JSON.stringify({ project_id: projectId }) });
    state.activeProject = body.active_project; await loadProjectData(); renderWorkspace();
    showToast(`Đã chuyển sang project “${state.activeProject.title}”.`);
  } catch (error) { renderWorkspace(); showToast(error.message, true); }
});

$("#new-project-button").addEventListener("click", async () => {
  const title = window.prompt("Tên video/project mới:");
  if (!title?.trim()) return;
  try {
    const body = await api("/api/projects", { method: "POST", body: JSON.stringify({ title: title.trim() }) });
    state.activeProject = body.active_project;
    const workspace = await api("/api/workspace");
    state.projects = workspace.projects; state.workspaceRoot = workspace.workspace_root;
    await loadProjectData(); renderWorkspace(); showToast(`Đã tạo project “${state.activeProject.title}”.`);
  } catch (error) { showToast(error.message, true); }
});

async function loadAll() {
  try {
    const [workspace, blocks, scenes, characters, periods, providers, ttsProviders, timeline, renderer] = await Promise.all([api("/api/workspace"), api("/api/blocks"), api("/api/scenes"), api("/api/characters"), api("/api/period-kits"), api("/api/image-providers"), api("/api/tts-providers"), api("/api/timeline"), api("/api/renderer")]);
    state.projects = workspace.projects; state.activeProject = workspace.active_project; state.workspaceRoot = workspace.workspace_root;
    state.imageProviders = providers.providers; state.activeProvider = providers.active_provider; state.imageAspectRatio = providers.aspect_ratio; state.imageQuality = providers.quality;
    state.ttsProviders = ttsProviders.providers; state.activeTTSProvider = ttsProviders.active_provider; state.ttsVoiceId = ttsProviders.voice_id; state.ttsStyle = ttsProviders.style; state.ttsSpeed = ttsProviders.speed; state.ttsEngineUrl = ttsProviders.engine_url;
    state.blocks = blocks.blocks; state.scenes = scenes.scenes; state.characters = characters.characters; state.periodKits = periods.period_kits; state.timeline = timeline.timeline; state.renderer = renderer;
    Object.keys(state.dirty).forEach((area) => setDirty(area, false));
    renderWorkspace(); renderBlocks(); renderCharacters(); renderPeriods(); renderScenes(); renderTimeline();
  } catch (error) { showToast(error.message, true); }
}

loadAll();
