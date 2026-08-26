// 最终总结阶段 → 合成 LaneView 兼容事件
// 把 summary_progress WS 消息翻译成 activeBlocks/finishedBlocks 操作，
// 让 LaneView 用同一组件显示总结阶段的 LLM 调用（分卷/复检/风格/报告）。
//
// 设计：不引入后端改动。summary 阶段的 batch_done/batch_failed 已通过 hub 广播，
// 只是没喂给 LaneView。本文件在 frontend 这层补上合成块的生命周期。
//
// 合成 id 范围（避开分析阶段 1-10000 的章节 id）：
//   batch 阶段  : 900001-900999  (卷分析并发批)
//   recheck 阶段: 910001-910999  (伏笔复检批)
//   style 阶段  : 920001         (单次 LLM)
//   report 阶段 : 930001         (单次 LLM)

export interface SummaryLaneBlock {
  range: string
  startedAt: number
}

export type SummaryLaneBlocks = Map<number, SummaryLaneBlock>

export type SummaryLaneFinished = Map<number, { ok: boolean; range: string }>

/**
 * 处理一条 summary_progress 事件，更新 active/finished Maps。
 * 注意：调用者负责保证返回的 Map 引用被替换为 new Map() 触发响应式。
 */
export function applySummaryProgress(
  p: Record<string, unknown>,
  active: SummaryLaneBlocks,
  finished: SummaryLaneFinished,
): { changed: boolean; newActive: SummaryLaneBlocks; newFinished: SummaryLaneFinished } {
  const ptype = String(p.type ?? '')
  const phase = String(p.phase ?? '')
  const totalBatches = Number(p.total_batches ?? 0)
  const batchIdx = Number(p.batch ?? 0)

  // 阶段开始：上一阶段残留的合成块（如果有）移到 finished 让用户看到 ✓，
  // 然后合成新阶段块
  if (ptype === 'phase' && (phase === 'batch' || phase === 'recheck' || phase === 'style' || phase === 'report')) {
    const now = Date.now()
    const next = new Map(active)
    const nextFinished = new Map(finished)
    // 上一阶段残留的合成块（id ≥ 900000 是总结阶段合成块；分析阶段 < 900000 不动）
    for (const [id, info] of next) {
      if (id >= 900000) {
        nextFinished.set(id, { ok: true, range: info.range })
        next.delete(id)
      }
    }
    if (phase === 'batch' && totalBatches > 0) {
      for (let i = 1; i <= totalBatches; i++) {
        next.set(900000 + i, { range: `卷${i}/${totalBatches}`, startedAt: now })
      }
    } else if (phase === 'recheck' && totalBatches > 0) {
      for (let i = 1; i <= totalBatches; i++) {
        next.set(910000 + i, { range: `伏笔复检批${i}/${totalBatches}`, startedAt: now })
      }
    } else if (phase === 'style') {
      next.set(920001, { range: '写作风格分析', startedAt: now })
    } else if (phase === 'report') {
      next.set(930001, { range: '生成全书脉络报告', startedAt: now })
    }
    return { changed: true, newActive: next, newFinished: nextFinished }
  }

  // 单批完成/失败：把对应合成块从 active 移到 finished
  if (ptype === 'batch_done' || ptype === 'batch_failed') {
    if (batchIdx > 0) {
      // batch 阶段用 900000+id（其他阶段没 batch_done 事件，id 不会冲突）
      const blockId = 900000 + batchIdx
      if (active.has(blockId)) {
        const next = new Map(active)
        const nextFinished = new Map(finished)
        next.delete(blockId)
        // range 补 total_batches 后缀（"卷3/12"），没传 total_batches 时退化为 "卷3"
        const totalLabel = totalBatches > 0 ? `${batchIdx}/${totalBatches}` : `${batchIdx}`
        nextFinished.set(blockId, {
          ok: ptype === 'batch_done',
          range: `卷${totalLabel}`,
        })
        return { changed: true, newActive: next, newFinished: nextFinished }
      }
    }
  }

  // 整个总结完成：style/report 等单次 LLM 没 batch_done，兜底标 done
  if (ptype === 'complete') {
    if (active.size > 0) {
      const next = new Map(active)
      const nextFinished = new Map(finished)
      for (const [id, info] of next) {
        if (id >= 900000) {
          nextFinished.set(id, { ok: true, range: info.range })
          next.delete(id)
        }
      }
      return { changed: true, newActive: next, newFinished: nextFinished }
    }
  }

  // 用户主动停止：后端只发 {"type": "status", "message": "已停止"}，
  // 不会发 complete。残留的合成块会一直留在 active 转圈（elapsed 一直涨），
  // 整行"运行中"但实际 LLM 已经停了。这里把 ≥900000 的活动块以 ok=false 移到 finished。
  if (ptype === 'status' && typeof p.message === 'string' && p.message.includes('已停止')) {
    if (active.size > 0) {
      const next = new Map(active)
      const nextFinished = new Map(finished)
      for (const [id, info] of next) {
        if (id >= 900000) {
          nextFinished.set(id, { ok: false, range: info.range })
          next.delete(id)
        }
      }
      return { changed: true, newActive: next, newFinished: nextFinished }
    }
  }

  return { changed: false, newActive: active, newFinished: finished }
}
