// 把 OpsRunner 的 TLA+ 变量翻译成操作人看得懂的标签。
// 时序图用 /statechart 生成；这里只负责文字与分组。

const DONE = ["succeeded", "awaiting_review", "cancelled", "orphaned"];

const ids = (v: any): string[] => Object.keys(v.db);
const kids = (v: any): string[] => ids(v).filter((j) => v.child[j]);
const some = (v: any, f: (j: string) => boolean): boolean => ids(v).some(f);

export const title = "操作台写任务 写者互斥与写锁";

export const state = (v: any): string => {
  const live = kids(v);
  const n = live.length;

  if (n >= 2) return "两个写者同时在写";
  if (!v.serverUp) return n === 1 ? "服务中断 孩子在跑" : "服务已停";
  if (v.httpOn && n === 1) return "账表读改写中 孩子在写";
  if (n === 1 && v.db[live[0]] === "orphaned")
    return v.flock === live[0] ? "孤儿任务 孩子还在写 锁在孩子手里" : "孤儿任务 孩子还在写 锁不在它手里";
  if (n === 1 && v.flock !== live[0]) return "写任务在跑 锁不在它手里";
  if (n === 1) return "写任务在跑 持锁";
  if (v.httpOn) return "账表读改写中";
  if (some(v, (j) => v.db[j] === "queued")) return "写任务排队";
  if (some(v, (j) => v.db[j] === "running" && v.released[j])) return "写任务收尾中";
  if (some(v, (j) => v.db[j] === "running")) return "任务已置运行 还没起进程";
  if (ids(v).every((j) => DONE.includes(v.db[j]))) return "全部收尾";
  return "空闲";
};

export const group = (v: any): string => {
  const s = state(v);
  if (s === "两个写者同时在写" || s.startsWith("孤儿任务") ||
      s === "账表读改写中 孩子在写" || s === "写任务在跑 锁是空的") return "违规";
  if (s === "服务中断 孩子在跑" || s === "服务已停") return "中断";
  return "正常";
};

export const colors = { 正常: "teal", 中断: "orange", 违规: "pink" };

const STEPS: { [action: string]: string } = {
  Submit: "操作人再提交一个写任务",
  Dispatch: "调度器起写任务",
  Acquire: "拿到工作区写锁",
  MarkRunning: "任务开始跑",
  AbortDispatched: "取消撞上启动 任务退出",
  Spawn: "拉起写进程",
  ExitChild: "写进程结束",
  CancelQueued: "操作人取消排队任务",
  CancelRunning: "操作人取消在跑任务",
  Release: "放下写锁 腾出槽位",
  Finish: "写入终态",
  AdoptFinish: "接管收尾 退出码未知",
  HttpStart: "操作台写账表",
  HttpEnd: "账表写完",
  Crash: "服务进程死掉",
  Start: "新服务启动 接管残留",
  Done: "收尾",
};

export const step = (action: string): string | null => {
  const name = action.replace(/\(.*\)$/, "");
  return STEPS[name] ?? null;
};
