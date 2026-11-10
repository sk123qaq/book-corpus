// The reader sees a chapter, not six TLA+ variables. These labels name what
// the reader can see: which path the badge claims, and whether the depth page
// answers. The rule is P1, AdvertisedDepthIsReadable.
export const title = '章节路径 - 徽章说深度时, 深度页是否可读';

export const state = (v) => {
  const hasDepthFiles = v.html || v.model || v.ledger;
  if (!hasDepthFiles && !v.legacy) return '空白章节';
  if (!hasDepthFiles) return '旧路径已出片';

  if (v.status === 'ready' && v.shaMatch && v.html) return '深度页可读';
  if (!v.shaMatch) {
    return v.html ? '源文已改, 深度页过期' : '源文已改, 深度模型待重建';
  }
  if (v.status === 'model' && v.html) return '结构模型重建, 旧深度页停用';
  if (v.status === 'model' && !v.html) return '结构模型已建, 深度页未出';
  return '深度文件在, 状态缺失';
};

export const step = (action) => {
  const name = String(action).split(/\s+/)[0];
  return {
    LegacyBuild: '旧四档任务出片',
    ModelOnlyBuild: '只重建结构模型',
    ReadyBuild: '生成深度层并渲染',
    SourceEdit: '原文被编辑',
    SourceRevert: '原文改回',
  }[name] || null;
};

export const group = (v) => {
  const hasDepthFiles = v.html || v.model || v.ledger;
  if (!hasDepthFiles) return v.legacy ? '旧路径' : '空白';
  if (v.status === 'ready' && v.shaMatch && v.html) return '深度路径';
  return '断链';
};

export const colors = {
  空白: 'tan',
  旧路径: 'orange',
  深度路径: 'teal',
  断链: 'pink',
};
