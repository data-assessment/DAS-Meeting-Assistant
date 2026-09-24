import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface SearchableOption {
  value: string
  label: string
}

interface Props {
  value: string
  options: SearchableOption[]
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  maxVisibleOptions?: number
  ariaLabel?: string
  emptyMessage?: string
  toggleLabel?: string
}

interface DropdownBox {
  left: number
  top: number
  width: number
  maxHeight: number
}

const GAP = 3
const MARGIN = 6
const PREFERRED_HEIGHT = 180

export function SearchableSelect({
  value,
  options,
  onChange,
  placeholder = 'Search…',
  disabled = false,
  maxVisibleOptions,
  ariaLabel,
  emptyMessage = 'No matching options',
  toggleLabel = 'Show options',
}: Props) {
  const listId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [box, setBox] = useState<DropdownBox | null>(null)
  const sortedOptions = useMemo(
    () => [...options].sort((a, b) => a.label.localeCompare(b.label, undefined, {
      numeric: true,
      sensitivity: 'base',
    })),
    [options],
  )
  const selected = sortedOptions.find((option) => option.value === value)
  const filteredOptions = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return needle
      ? sortedOptions.filter((option) => option.label.toLocaleLowerCase().includes(needle))
      : sortedOptions
  }, [query, sortedOptions])
  const visibleOptions = useMemo(
    () => maxVisibleOptions == null
      ? filteredOptions
      : filteredOptions.slice(0, maxVisibleOptions),
    [filteredOptions, maxVisibleOptions],
  )

  // The dialog body is a scroll container, so an absolutely positioned list is
  // clipped at its edge. Render the list in a portal and place it with viewport
  // coordinates instead, flipping above the field when there is no room below.
  const placeDropdown = useCallback(() => {
    const anchor = rootRef.current
    if (!anchor) return
    const rect = anchor.getBoundingClientRect()
    const below = window.innerHeight - rect.bottom - GAP - MARGIN
    const above = rect.top - GAP - MARGIN
    const flipUp = below < Math.min(PREFERRED_HEIGHT, above)
    const maxHeight = Math.max(72, Math.min(PREFERRED_HEIGHT, flipUp ? above : below))
    setBox({
      left: Math.max(MARGIN, Math.min(rect.left, window.innerWidth - rect.width - MARGIN)),
      top: flipUp ? Math.max(MARGIN, rect.top - GAP - maxHeight) : rect.bottom + GAP,
      width: rect.width,
      maxHeight,
    })
  }, [])

  useLayoutEffect(() => {
    if (!open) { setBox(null); return }
    placeDropdown()
    const reflow = () => placeDropdown()
    window.addEventListener('resize', reflow)
    window.addEventListener('scroll', reflow, true)
    return () => {
      window.removeEventListener('resize', reflow)
      window.removeEventListener('scroll', reflow, true)
    }
  }, [open, placeDropdown, visibleOptions.length])

  useEffect(() => {
    const close = (event: MouseEvent) => {
      const target = event.target as Node
      if (rootRef.current?.contains(target) || listRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  useEffect(() => {
    if (!open) return
    listRef.current?.querySelector('button.active')?.scrollIntoView({ block: 'nearest' })
  }, [open, activeIndex, visibleOptions])

  const choose = (option: SearchableOption) => {
    onChange(option.value)
    setQuery('')
    setOpen(false)
    setActiveIndex(0)
  }

  return (
    <div className="search-select" ref={rootRef}>
      <input
        type="text"
        ref={inputRef}
        role="combobox"
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && visibleOptions[activeIndex] ? `${listId}-${activeIndex}` : undefined}
        disabled={disabled}
        placeholder={placeholder}
        value={open ? query : (selected?.label ?? '')}
        onFocus={() => { setQuery(''); setOpen(true); setActiveIndex(0) }}
        onBlur={(event) => {
          // Tabbing away must not leave an orphaned list floating over the dialog.
          // Option clicks keep focus here (their mousedown is prevented), so this
          // only fires for focus genuinely leaving the control.
          const next = event.relatedTarget as Node | null
          if (next && (rootRef.current?.contains(next) || listRef.current?.contains(next))) return
          setQuery('')
          setOpen(false)
        }}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); setActiveIndex(0) }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setOpen(true)
            setActiveIndex((index) => open ? Math.min(index + 1, Math.max(visibleOptions.length - 1, 0)) : 0)
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActiveIndex((index) => Math.max(index - 1, 0))
          } else if (event.key === 'Enter' && open && visibleOptions[activeIndex]) {
            event.preventDefault()
            choose(visibleOptions[activeIndex])
          } else if (event.key === 'Escape') {
            if (open) { event.preventDefault(); event.stopPropagation() }
            setQuery('')
            setOpen(false)
          }
        }}
      />
      <button
        type="button"
        className="search-select-toggle"
        tabIndex={-1}
        aria-label={toggleLabel}
        disabled={disabled}
        onMouseDown={(event) => {
          // Keep focus (and therefore typing and arrow keys) in the input; a plain
          // click would blur it and race the open/close state.
          event.preventDefault()
          const wasOpen = open
          inputRef.current?.focus()
          setQuery('')
          setActiveIndex(0)
          setOpen(!wasOpen)
        }}
      >▾</button>
      {open && !disabled && box && createPortal(
        <div
          className="search-select-options"
          role="listbox"
          id={listId}
          aria-label={ariaLabel}
          ref={listRef}
          style={{ left: box.left, top: box.top, width: box.width, maxHeight: box.maxHeight }}
        >
          {visibleOptions.length ? visibleOptions.map((option, index) => (
            <button
              type="button"
              role="option"
              id={`${listId}-${index}`}
              tabIndex={-1}
              aria-selected={option.value === value}
              className={index === activeIndex ? 'active' : ''}
              key={option.value}
              title={option.label}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(option)}
            >{option.label}</button>
          )) : <div className="search-select-empty" role="status">{emptyMessage}</div>}
          {visibleOptions.length < filteredOptions.length && (
            <div className="search-select-empty">
              Showing {visibleOptions.length} of {filteredOptions.length}; type to narrow results
            </div>
          )}
        </div>,
        rootRef.current?.closest('dialog') ?? document.body,
      )}
    </div>
  )
}
