function extToMime(path) {
  const lower = (path || "").toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  return "image/jpeg";
}

function filePathToDataUrl(filePath) {
  return new Promise((resolve, reject) => {
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

  const files = chooseRes.tempFiles || [];
  const output = [];
  for (const item of files) {
    const path = item.tempFilePath;
    const dataUrl = await filePathToDataUrl(path);
    output.push({ path, dataUrl });
  }
  return output;
}

module.exports = {
  pickImages,
  filePathToDataUrl
};
