# CG-00 命令目录与最终执行状态

状态：**COMPLETE / R6 QUALIFIED**。以下是固定版本的实际命令结果，不用最新 CLI 替代旧消费者。

## 1. 执行环境

- Go：`go1.25.13 linux/amd64`
- `GOTOOLCHAIN=local`
- `GOPROXY=https://proxy.golang.org,direct`
- `GOSUMDB=sum.golang.org`
- `GOFLAGS=-mod=readonly`
- 固定来源：Biz `fed9051…` + Yunka `33b98ce…`；IoT `bcd20632…` + Yunka `057ebcf…`
- Run：`34553907760`；Job：`103122355997`；Artifact：`10181806958`

## 2. 六条 CLI 调用

```bash
# Biz pinned CLI
GOWORK="$WORKSPACE/yunka.io/go.work" go -C "$WORKSPACE/yunka.io/app" run ./cmd --help
GOWORK="$WORKSPACE/yunka.io/go.work" go -C "$WORKSPACE/yunka.io/app" run ./cmd context --help
GOWORK="$WORKSPACE/yunka.io/go.work" go -C "$WORKSPACE/yunka.io/app" run ./cmd context --root "$WORKSPACE/biz" --json

# IoT pinned CLI
GOWORK="$WORKSPACE/iot-delivery-system/third_party/yunka/go.work" go -C "$WORKSPACE/iot-delivery-system/third_party/yunka/app" run ./cmd --help
GOWORK="$WORKSPACE/iot-delivery-system/third_party/yunka/go.work" go -C "$WORKSPACE/iot-delivery-system/third_party/yunka/app" run ./cmd context --help
GOWORK="$WORKSPACE/iot-delivery-system/third_party/yunka/go.work" go -C "$WORKSPACE/iot-delivery-system/third_party/yunka/app" run ./cmd context --root "$WORKSPACE/iot-delivery-system" --json
```

结果：

```text
biz-help          0  PASS
biz-context-help  1  NOT_APPLICABLE_PINNED_CLI_COMMAND_ABSENT
biz-context-json  1  NOT_APPLICABLE_PINNED_CLI_COMMAND_ABSENT
iot-help          0  PASS
iot-context-help  0  PASS
iot-context-json  0  PASS (schemaVersion=4)
```

Biz `33b98ceb…` 顶层 help 的 Diagnostics/inspection 列表没有 `context`；两个 context 调用 stderr 均为 `No help topic for 'context'`。因此这里的非零退出码是**固定版本能力边界**，不是缺依赖、CLI 未启动或验收失败。

## 3. Biz 完整 workspace

```bash
GOWORK="$WORKSPACE/biz/go.work" bash "$WORKSPACE/biz/scripts/consumer-resolution-check.sh"
```

退出码 `0`，stdout：

```text
dependency resolution check: Biz-local workspace equals GOWORK=off graph
```

## 4. 只读性

在命令前后对 Biz、Biz Yunka、IoT、IoT Yunka 中所有 `go.mod/go.sum/go.work/go.work.sum` 计算摘要，diff 为空；四个工作树最终均为空状态。

## 5. 仍不属于 PASS 的 legacy 范围

IoT legacy `go.mod` 的 `github.com/go-kit/kit` replace 指向 `third_party/yunka/compat/go-kit-kit-log`，固定 `057ebcf…` 中该目录不存在。此项继续 `INCOMPLETE`，不能用 backend-yunka 当前模块 PASS 替代。
