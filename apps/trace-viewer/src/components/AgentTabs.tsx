import React from 'react'
import type { AgentInfo } from '../lib/parser'

interface AgentTabsProps {
  agents: AgentInfo[]
  activeAgent: string
  onSelectAgent: (scope: string) => void
}

const AgentTabs: React.FC<AgentTabsProps> = ({ agents, activeAgent, onSelectAgent }) => {
  // Don't render if there's only a main agent (no subagents)
  if (agents.length <= 1) return null

  return (
    <div
      className="flex items-center gap-1 px-3 py-1 border-b overflow-x-auto"
      style={{
        borderColor: 'var(--divider)',
        background: 'var(--surface)',
        minHeight: 32,
        scrollbarWidth: 'thin',
      }}
    >
      {agents.map((agent) => {
        const isActive = agent.scope === activeAgent
        return (
          <button
            key={agent.scope}
            onClick={() => onSelectAgent(agent.scope)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] cursor-pointer whitespace-nowrap shrink-0"
            style={{
              background: isActive ? 'var(--info)' : 'var(--surface-hover)',
              color: isActive ? '#fff' : 'var(--muted)',
              border: 'none',
              transition: 'background 0.1s, color 0.1s',
            }}
          >
            <span>{agent.label}</span>
            <span
              className="text-[9px] px-1 py-0 rounded-full"
              style={{
                background: isActive ? 'rgba(255,255,255,0.25)' : 'var(--divider)',
                color: isActive ? '#fff' : 'var(--muted)',
                minWidth: 16,
                textAlign: 'center',
              }}
            >
              {agent.eventCount}
            </span>
          </button>
        )
      })}
    </div>
  )
}

export default React.memo(AgentTabs)
