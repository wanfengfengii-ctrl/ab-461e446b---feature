# Toolpath Audit Service

精密探针程序下发运动控制器前的离线审计：校验 G 代码、把轨迹统一换算为毫米
线段、确认整条轨迹（不只是终点）留在闭合工作空间内、且不接触任何夹具禁入
长方体的内部或边界。

## 接口

### `GET /health`

健康检查，返回 `{"status": "ok"}`，供容器 HEALTHCHECK / Compose
`condition: service_healthy` 使用。

### `POST /api/toolpaths/audit`

请求体（JSON）：

| 字段 | 说明 |
| --- | --- |
| `initial_position_mm` | `{x,y,z}`，初始毫米坐标，必须位于工作空间闭区间内 |
| `workspace` | `{min:{x,y,z}, max:{x,y,z}}`，闭合工作空间 |
| `forbidden_regions` | 至多 20 个 `{bounds:{min,max}}` 闭合长方体（可为空数组） |
| `program` | G 代码字符串，至多 5000 行 |
| `controller_steps_mm` | 可选 `{x,y,z}`，三轴**正**的规范十进制毫米步距；省略时行为与既有契约一致 |

成功 `200`：

```json
{
  "status": "accepted",
  "segments": [
    {"start": {"x": "0", "y": "0", "z": "0"},
     "end":   {"x": "25.4", "y": "0", "z": "0"},
     "motion": "G0", "line": 1}
  ],
  "final_position_mm": {"x": "25.4", "y": "0", "z": "0"}
}
```

失败：

* `400 invalid_request` — 请求结构/数值问题；
* `422 program_error` — 程序词法/语义错误，带首个违规 `line` 与 `reason`；
* `422 outside_workspace` — 线段端点越出闭合工作空间，带 `line`；
* `422 forbidden_contact` — 线段接触禁入区内部或边界，带 `line` 与
  `forbidden_region`（1 基编号）。

任何失败都不返回 `segments` / `final_position_mm`，即不存在可下发的部分轨迹。

## 控制器步距量化（可选）

部分运动控制器只能按固定轴步距执行：理想毫米坐标虽通过审计，量化后的实际
轨迹仍可能接触夹具。请求携带 `controller_steps_mm: {x, y, z}`（三轴均为正的
规范十进制毫米步距）时启用按步距量化：

* 初始坐标必须落在各轴步距网格上，否则以 `400 invalid_request` 拒绝；
* 每个坐标行仍先按 G20/G21、G90/G91 求得轴值，再把本行给出的绝对坐标
  （G90）或相对位移（G91）舍入到最近整步；恰好在半步处沿远离零方向取整
  （如 `-2.5` → `-3`、`2.5` → `3`）；
* 行内未出现的轴保持实际位置；相对运动从上一条**量化后**的实际位置累加；
* 成功响应的 `segments` 与 `final_position_mm` 即为可下发轨迹，工作空间与
  禁入区也按该量化轨迹检查，并继续报告最早违规的原始行号；
* 省略该字段时，接口行为与既有契约完全一致（返回理想毫米坐标）。

## G 代码规则

* 仅接受：`G20`（英寸）/`G21`（毫米）、`G90`（绝对）/`G91`（相对）、
  `G0`/`G1` 直线运动、`X/Y/Z` 规范十进制参数；
* 参数须为规范有限十进制（如 `1`、`-2.5`、`1.0e2`），拒绝 `NaN`、
  `Infinity`、下划线、十六进制；
* 分号 `;` 起到行尾为注释；空行与纯注释行不产生运动；
* 模态设置跨行持续生效；同一行的新设置先作用于该行坐标；
* 同一行同类模态冲突（G20/G21、G90/G91、G0/G1）、轴重复、非法词均报错；
* 未建立运动/单位/坐标模式就给出坐标属于错误；
* 所有错误定位到**原始行号**（从 1 开始）。

## 精确性

全部几何运算使用 `fractions.Fraction`：25.4 = 127/5，英寸换算与相对移动
累加均为精确有理数；线段-长方体相交采用有理数 slab 裁剪（Liang-Yu），
斜穿、贴面、穿过棱/角都稳定裁决。输出经十进制精确还原为规范字符串（分母只
含因子 2、5，必然有限）。

## 运行

```bash
# 宿主机端口可配置（默认 8080）
HOST_PORT=9090 docker compose up --build api
curl -s localhost:9090/health
```

## 一次性 verify 服务

`verify` 服务等待 `api` 健康后，在容器内依次完成：字节码构建检查、
`pytest` 代码测试（含全部边界/碰撞/步距量化用例）、含**英寸相对移动**、
斜向碰撞与**控制器步距量化**（毫米绝对、英寸相对、半步负值取整、量化后
触碰夹具、未启用选项回归）的 API 冒烟，并以退出码报告结果：

```bash
docker compose build
docker compose up --build verify      # 退出码 0 表示全部通过
docker compose rm -f verify           # 清理一次性容器
```
