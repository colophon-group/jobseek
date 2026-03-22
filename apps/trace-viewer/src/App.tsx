import React, { useState, useEffect, useCallback } from 'react'
import { useTrace } from './hooks/useTrace'
import TopBar from './components/TopBar'
import Timeline from './components/Timeline'
import DetailPanel from './components/DetailPanel'
import FilterBar from './components/FilterBar'
import TokenChart from './components/TokenChart'
import UploadZone from './components/UploadZone'
import TraceSidebar from './components/TraceSidebar'

function App() {
  const {
    events,
    allEvents,
    stats,
    selected,
    selectedEvent,
    setSelected,
    filter,
    setFilter,
    search,
    setSearch,
    loadJsonl,
    filename,
    bundles,
    activeBundle,
    activeHeader,
    activateBundle,
    serverLoaded,
    serverAttempted,
  } = useTrace()

  const [darkMode, setDarkMode] = useState(() => {
    const stored = localStorage.getItem('trace-viewer-dark')
    if (stored !== null) return stored === 'true'
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
    localStorage.setItem('trace-viewer-dark', String(darkMode))
  }, [darkMode])

  const toggleDark = useCallback(() => setDarkMode((d) => !d), [])

  const handleClickTurn = useCallback(
    (eventId: number) => {
      setSelected(eventId)
    },
    [setSelected]
  )

  const isLoaded = stats !== null
  const hasBundles = bundles.length > 0

  return (
    <div className="flex flex-col h-screen" style={{ background: 'var(--background)' }}>
      <TopBar
        stats={stats}
        filename={filename}
        activeHeader={activeHeader}
        search={search}
        onSearchChange={setSearch}
        onLoad={loadJsonl}
        darkMode={darkMode}
        onToggleDark={toggleDark}
      />

      {!isLoaded && serverAttempted ? (
        <UploadZone onLoad={loadJsonl} />
      ) : !isLoaded ? (
        /* Still loading from server -- show nothing while waiting */
        <div
          className="flex items-center justify-center h-full text-sm"
          style={{ color: 'var(--muted)' }}
        >
          Loading traces...
        </div>
      ) : (
        <div className="flex flex-1 min-h-0">
          {/* Left panel: timeline */}
          <div
            className="flex flex-col"
            style={{
              width: '40%',
              minWidth: 320,
              maxWidth: 560,
              borderRight: '1px solid var(--divider)',
            }}
          >
            <TokenChart events={allEvents} onClickTurn={handleClickTurn} />
            <Timeline events={events} selected={selected} onSelect={setSelected} />
            <FilterBar
              filter={filter}
              onFilterChange={setFilter}
              eventCount={events.length}
              totalCount={allEvents.length}
            />
          </div>

          {/* Center panel: detail */}
          <div className="flex-1 min-w-0" style={{ background: 'var(--surface)' }}>
            <DetailPanel event={selectedEvent} />
          </div>

          {/* Right panel: trace explorer sidebar */}
          {hasBundles && (
            <TraceSidebar
              bundles={bundles}
              activeBundle={activeBundle}
              onSelectBundle={activateBundle}
              onUpload={loadJsonl}
            />
          )}
        </div>
      )}
    </div>
  )
}

export default App
