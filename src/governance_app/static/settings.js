import { fetchJson, postJson } from "/api.js?v=20260517-1";
import { escapeHtml, withBusy } from "/ui.js?v=20260517-1";

export async function renderSettings({ mainContent, shellHeader }) {
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("本地设置", "本机配置")}
      <div id="settings-summary" class="settings-summary">正在加载设置</div>
    </section>
    <section class="card">
      ${shellHeader("备份恢复", "数据安全")}
      <div class="operation-panel">
        <div class="button-row">
          <button id="create-backup" class="secondary-button" type="button">创建备份</button>
        </div>
        <div class="form-grid">
          <label class="form-field">
            <span>备份文件路径</span>
            <input id="restore-path" placeholder="/Users/.../backups/governance-xxxx.sqlite3">
          </label>
        </div>
        <button id="restore-backup" class="primary-button" type="button">恢复备份</button>
      </div>
      <div id="settings-result" class="result-box">等待操作</div>
    </section>
    <section class="card">
      ${shellHeader("系统复位", "初始化")}
      <div class="operation-panel">
        <p>复位会清除批次、台账、稽核问题、回传记录和当前批次选择。默认保留导出文件和备份文件。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>确认文字</span>
            <input id="reset-confirmation" placeholder="输入：复位">
          </label>
          <label class="check-field">
            <input id="preserve-exports" type="checkbox" checked>
            <span>保留导出文件</span>
          </label>
          <label class="check-field">
            <input id="preserve-backups" type="checkbox" checked>
            <span>保留备份文件</span>
          </label>
        </div>
        <button id="reset-system" class="danger-button" type="button">执行复位</button>
      </div>
    </section>
  `;
  await loadSettings();
  document.querySelector("#create-backup").addEventListener("click", async (event) => {
    await withBusy(event.currentTarget, "备份中...", async () => {
      const data = await postJson("/api/backup", {});
      setSettingsResult("success", `备份已创建：${data.path}`);
    });
  });
  document.querySelector("#restore-backup").addEventListener("click", async (event) => {
    const path = document.querySelector("#restore-path").value.trim();
    if (!path) {
      setSettingsResult("error", "请填写备份文件路径");
      return;
    }
    await withBusy(event.currentTarget, "恢复中...", async () => {
      const data = await postJson("/api/restore", { path });
      setSettingsResult("success", `恢复完成；恢复前安全备份：${data.safety_backup_path}`);
      await loadSettings();
    });
  });
  document.querySelector("#reset-system").addEventListener("click", async (event) => {
    const confirmation = document.querySelector("#reset-confirmation").value.trim();
    if (confirmation !== "复位") {
      setSettingsResult("error", "请输入“复位”确认后再执行");
      return;
    }
    await withBusy(event.currentTarget, "复位中...", async () => {
      const data = await postJson("/api/reset", {
        confirmation,
        preserve_exports: document.querySelector("#preserve-exports").checked,
        preserve_backups: document.querySelector("#preserve-backups").checked,
      });
      setSettingsResult("success", `复位完成；安全备份：${data.safety_backup_path}`);
      await loadSettings();
    });
  });
}

async function loadSettings() {
  const data = await fetchJson("/api/settings");
  document.querySelector("#settings-summary").innerHTML = `
    <div class="settings-grid">
      <span><strong>工作目录</strong>${escapeHtml(data.workspace_dir)}</span>
      <span><strong>数据库</strong>${escapeHtml(data.database_path)}</span>
      <span><strong>导出目录</strong>${escapeHtml(data.export_dir)}</span>
      <span><strong>备份目录</strong>${escapeHtml(data.backup_dir)}</span>
      <span><strong>模板版本</strong>${escapeHtml(data.template_version)}</span>
    </div>
  `;
}

function setSettingsResult(stateName, content) {
  const result = document.querySelector("#settings-result");
  result.className = `result-box result-${stateName}`;
  result.textContent = content;
}
