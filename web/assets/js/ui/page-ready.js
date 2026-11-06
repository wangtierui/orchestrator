/* ui/page-ready.js —— 渲染完成标记与轮询等待（实现当前在 core.js，此处做模块边界；
 * P5 阶段会连同字面值一起真正搬进来，公开 API 不变） */
export { markReady, when } from "./core.js";
