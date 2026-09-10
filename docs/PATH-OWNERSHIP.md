# CG-00 路径与所有权冻结

状态：**R5；仅四文档可公开更新；消费者只读；动态 Ownership 快照尚未取得。**

## 1. 精确写入范围

治理仓库 `hvritual/engineering-governance`：

```text
docs/CG-CHARTER.md
docs/BASELINES.json
docs/PATH-OWNERSHIP.md
docs/COMMAND-CATALOG.md
```

公开发布已获授权，不包括原始日志、源码、凭据、新工作流、规则代码、工具锁或模板。证据、缓存、工具安装及源码恢复位于受审源码树外，不属于新增治理产品功能。

| 对象 | 权限 |
|---|---|
| Yunka 框架参考和两套锁定源码 | 只读；不升级 CLI、生成器或 Runtime |
| Biz 全部业务、配置、依赖、generated | 只读 |
| IoT backend、backend-yunka、web、third_party | 只读 |
| IoT server/** | 永久禁止写入，即使当前未出现该路径 |
| 消费者/治理工作流、仓库权限与保护 | 不修改 |
| 独立工具、缓存、私有证据目录 | 仅恢复环境和保留执行证据 |

已有只读/诊断 job 的重跑不等于修改其工作流。不得把旧发布、合并或迁移 job 当成只读恢复工具重跑。

## 2. 实际恢复布局

R5 已存在的隔离云端布局（根路径为会话工作区，不依赖任何用户桌面路径）：

```text
<CG_ROOT>/
  engineering-governance/                    # 四文档 Git 工作区
  sources/framework-reference/               # cc546c7d…
  sources/biz/                               # fed9051b…；完整当前树、浅历史
  sources/yunka.io/                          # 33b98ceb…；供 Biz 相邻 replace
  sources/iot-delivery-system/                # bcd20632…
    third_party/yunka/                       # 057ebcf8…
  toolchain/go-1.25.13/                       # 已核验并实际执行
  caches/                                   # 消费者根之外
  transports/                               # 仅作传输的原始归档
  evidence/                                 # 原始 argv/CWD/退出码/输出
```

源码树由可核验归档/bundle 和原始 Git 对象恢复；恢复过程不是产品修复。每个最终 tree 都与 GitHub 固定 tree 相同，不将源码摘录或新建替代工程当作现有源码。Biz 的浅历史限制必须保留；需要祖先历史时先恢复历史。

## 3. 权限与解析事实

后续适用 Yunka 的文件写权取交集：任务授权 ∩ Yunka Ownership ∩ 工具管理范围；全局拒绝优先。未知 Ownership 不推定可写；CG-00 对三产品仓库的写入集合为空。

Biz `.yunka/project.json` 指向 `contracts/sources.json`、`contracts/generated`、`modules`、`internal`；IoT 指向 `backend-yunka/contracts/proto`、`backend-yunka/contracts/generated`、`backend-yunka/modules`、`backend-yunka/internal`。输出根不是整个目录的 generated-only 判定，也不是手写权限。

Biz 和 IoT 当前模块的 Replace.Dir 已核对到各自固定源码目录。IoT legacy 的 go-kit 替换目录实际不存在；另外两个旧模块名与目标 go.mod 声明不同，按原样保留、不猜测兼容。仅 `go list -m` 成功不能证明完整包图或运行行为正确；不改变 legacy 模块的已知未完成资格。

## 4. 依赖与执行隔离

消费者 `go.mod/go.sum/go.work/go.work.sum` 和前端锁文件不得被治理工具污染。R5 前后 60 个角色/路径摘要记录相等、五个角色最终 clean；嵌套框架文件同时出现在消费者和框架角色记录中，因此 60 是记录数，不声称是 60 个互不重叠物理文件。

使用消费者各自固定的 framework workspace，不临时创建 go.work 改变解析。缓存、GOPATH、临时目录置于审计根外；GOROOT 不从外部强行覆盖。离线缺缓存返回阻断，不能把空报告当作通过。

Biz `make generate` 会调用 `go mod tidy` 与商业目录写入；IoT `yunka-generate` 会写生成产物，均不作为本次只读采集命令执行。没有对 generated 手工修补。

## 5. 独立脚手架的后续边界

后续允许引入适合特定问题的外部规则工具、迁移工具或专用脚手架；不是 CG-00 安装任务。模板必须明确拥有自己的路径，不与 Yunka 生成文件冲突，不伪造模板历史、不默认执行未批准脚本。合法新目录若不在现有 Ownership 内，优先放到相邻独立工程，而不是放宽 Yunka。

本轮没有新建规则引擎、模板平台、第二架构事实源或交付协议。原 R1–R4 细节在 `6b7609c9df35524ea4e4378b4d7ce855b1021ca9` 四文档快照与私有 bundle 中保留。
