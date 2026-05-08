/**
 * components/plan/PlanStream.tsx — Main plan rendering component.
 *
 * CONCEPT: Progressive rendering
 * --------------------------------
 * The plan arrives as a stream — sections appear one by one.
 * We render each section as soon as it's complete in the parsed plan.
 * Sections with no content yet simply don't render.
 *
 * This creates a "being written in real time" effect that's far more
 * engaging than a spinner followed by a full page appearing at once.
 *
 * CONCEPT: Conditional rendering based on stream status
 * -------------------------------------------------------
 *   idle      → show empty state with instructions
 *   streaming → show sections as they arrive (progressive)
 *   done      → show complete plan with action buttons
 *   error     → show error message with retry option
 */

'use client'

import type { StreamState } from '@/types'
import { TimelineCard } from './TimelineCard'
import { DineoutCard } from './DineoutCard'
import { FoodCard } from './FoodCard'
import { InstamartCard } from './InstamartCard'
import { OffersCard } from './OffersCard'
import { CostCard } from './CostCard'
import { ChatPanel } from './ChatPanel'

interface Props {
  streamState: StreamState
  onReset: () => void
  eventData?: Record<string, unknown>
}

export function PlanStream({ streamState, onReset, eventData = {} }: Props) {
  const { status, plan } = streamState

  // Empty state — no plan generated yet
  if (status === 'idle') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 opacity-30">
        <div className="font-display text-5xl italic text-ink-300">✦</div>
        <p className="font-display text-2xl italic">Your evening, curated.</p>
        <p className="text-sm text-center text-ink-500 max-w-64 leading-relaxed">
          Configure your event on the left and let Soirée orchestrate everything.
        </p>
      </div>
    )
  }

  // Error state
  if (status === 'error') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <p className="text-coral-400 text-sm">{streamState.error}</p>
        <button onClick={onReset} className="text-xs text-ink-500 hover:text-ink-300 transition-colors">
          Try again
        </button>
      </div>
    )
  }

return (
  <div className="flex flex-col gap-6 max-w-2xl">

    {/* While streaming — show raw text with cursor */}
    {status === 'streaming' && (
      <div className="animate-fade-up">
        <pre className="text-sm text-ink-300 whitespace-pre-wrap font-sans
          leading-relaxed streaming-cursor">
          {streamState.rawText
            .replace(/data: /g, '')  // strip SSE prefix if any leaked through
          }
        </pre>
      </div>
    )}

    {/* When done — show parsed cards */}
    {status === 'done' && plan && (
      <>
        {plan.brief && (
          <p className="font-display text-xl italic text-ink-200 leading-relaxed animate-fade-up">
            {plan.brief}
          </p>
        )}
        {plan.timeline.length > 0 && <TimelineCard steps={plan.timeline} />}
        {plan.dineout && <DineoutCard content={plan.dineout} />}
        {plan.food && <FoodCard content={plan.food} />}
        {plan.instamart && <InstamartCard content={plan.instamart} />}
        {plan.offers && <OffersCard content={plan.offers} totalSavings={plan.totalSavings} />}
        {plan.cost && <CostCard content={plan.cost} totalCost={plan.totalCost} />}
      </>
    )}

    {status === 'done' && (
      <div className="flex gap-3 mt-2 animate-fade-up">
        <button onClick={onReset}
          className="px-5 py-2.5 rounded-xl border border-white/8 text-sm text-ink-400
            hover:border-white/20 hover:text-ink-200 transition-all">
          ↺ New plan
        </button>
        <button className="px-5 py-2.5 rounded-xl bg-coral-500/10 border border-coral-500/30
          text-sm text-coral-400 hover:bg-coral-500/15 transition-all">
          ✓ Approve & Order
          <span className="ml-2 text-[10px] text-coral-500/50">Phase 2</span>
        </button>
      </div>
    )}
      {/* Follow-up chat — shown when plan is complete */}
      {status === 'done' && (
        <ChatPanel eventData={eventData} />
      )}

  </div>
)
}
