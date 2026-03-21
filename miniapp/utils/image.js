const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

function extToMime(path) {
  const lower = (path || "").toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  return "image/jpeg";
}

function filePathToDataUrl(filePath) {
  return new Promise((resolve, reject) => {
    if (typeof filePath !== "string" || !filePath.trim()) {
      reject(new Error("invalid image file path"));
      return;
    }
    const fs = wx.getFileSystemManager();
    fs.readFile({
      filePath,
      encoding: "base64",
      success(res) {
        const mime = extToMime(filePath);
        resolve(`data:${mime};base64,${res.data}`);
      },
      fail(err) {
        reject(new Error(err.errMsg || "读取图片失败"));
      }
    });
  });
}

function estimateDataUrlBytes(value) {
  const parts = String(value || "").split(",", 2);
  if (parts.length !== 2) return 0;
  const payload = String(parts[1] || "").trim();
  if (!payload) return 0;
  let padding = 0;
  if (payload.endsWith("==")) padding = 2;
  else if (payload.endsWith("=")) padding = 1;
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

function collectCandidatePaths(chooseRes) {
  const paths = [];
  const pushPath = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed) return;
    paths.push(trimmed);
  };

  if (chooseRes && Array.isArray(chooseRes.tempFilePaths)) {
    chooseRes.tempFilePaths.forEach(pushPath);
  }
  if (chooseRes && Array.isArray(chooseRes.apFilePaths)) {
    chooseRes.apFilePaths.forEach(pushPath);
  }
  if (chooseRes && Array.isArray(chooseRes.filePaths)) {
    chooseRes.filePaths.forEach(pushPath);
  }

  const files = chooseRes && Array.isArray(chooseRes.tempFiles) ? chooseRes.tempFiles : [];
  for (const item of files) {
    if (typeof item === "string") {
      pushPath(item);
      continue;
    }
    if (!item || typeof item !== "object") continue;
    pushPath(item.tempFilePath);
    pushPath(item.path);
    pushPath(item.filePath);
  }

  return [...new Set(paths)];
}

async function pickImages(count = 6) {
  const chooseRes = await new Promise((resolve, reject) => {
    wx.chooseImage({
      count,
      sizeType: ["compressed"],
      sourceType: ["album", "camera"],
      success: resolve,
      fail: reject
    });
  });

  const candidatePaths = collectCandidatePaths(chooseRes).slice(0, Math.max(1, count));
  if (candidatePaths.length === 0) {
    throw new Error("no valid image file selected");
  }

  const output = [];
  let lastErr = null;
  for (const path of candidatePaths) {
    try {
      const dataUrl = await filePathToDataUrl(path);
      if (estimateDataUrlBytes(dataUrl) > MAX_IMAGE_BYTES) {
        lastErr = new Error("单张图片不能超过 5MB");
        continue;
      }
      output.push({ path, dataUrl });
    } catch (err) {
      lastErr = err;
    }
  }

  if (output.length === 0) {
    throw lastErr || new Error("failed to read selected image");
  }
  return output;
}

module.exports = {
  pickImages,
  filePathToDataUrl
};
