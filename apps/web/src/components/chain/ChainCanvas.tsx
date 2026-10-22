import '@xyflow/react/dist/style.css'
import {
  Background,
  ReactFlow,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type OnEdgesChange,
  type OnNodesChange,
} from '@xyflow/react'
import { Maximize, ZoomIn, ZoomOut } from 'lucide-react'
import { useEffect, useRef, type CSSProperties, type DragEvent, type ReactNode } from 'react'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { DRAG_TYPE } from './flow'
import { ConditionNode, StartNode, StepNode } from './nodes'

const NODE_TYPES = { step: StepNode, condition: ConditionNode, start: StartNode }

/** The whole chain in view, however long; zoom in from there. */
const FIT = { padding: 0.12, maxZoom: 1, minZoom: 0.15 }

const FLOW_STYLE = {
  '--xy-background-color': 'var(--bg)',
  '--xy-background-pattern-color': 'var(--border-strong)',
  '--xy-edge-stroke': 'var(--border-strong)',
  '--xy-edge-stroke-selected': 'var(--accent)',
  '--xy-connectionline-stroke': 'var(--accent)',
  '--xy-node-background-color': 'transparent',
  '--xy-node-border': 'none',
  '--xy-node-boxshadow-selected': 'none',
  '--xy-handle-background-color': 'var(--border-strong)',
} as CSSProperties

function ZoomButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-9 place-items-center rounded-control border border-line-strong bg-panel text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
    >
      {children}
    </button>
  )
}

/** The canvas itself; inside a `ReactFlowProvider`. */
export function ChainCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  isValidConnection,
  onSelect,
  onDropItem,
  readOnly = false,
  footer,
  label,
}: {
  nodes: Node[]
  edges: Edge[]
  onNodesChange?: OnNodesChange
  onEdgesChange?: OnEdgesChange
  onConnect?: (c: Connection) => void
  isValidConnection?: (c: Connection | Edge) => boolean
  onSelect: (id: string | null) => void
  /** A palette item dropped at a canvas position. */
  onDropItem?: (key: string, position: { x: number; y: number }) => void
  readOnly?: boolean
  footer?: ReactNode
  label: string
}) {
  const t = useT()
  const flow = useReactFlow()
  const theme = useStore((s) => s.theme)
  // Fitted on open (`fitView`) and whenever a step comes or goes, so a new step is in view.
  const count = useRef(nodes.length)
  useEffect(() => {
    if (nodes.length === count.current) return
    count.current = nodes.length
    const id = window.setTimeout(() => void flow.fitView({ ...FIT, duration: 200 }), 50)
    return () => window.clearTimeout(id)
  }, [flow, nodes.length])

  const drop = (e: DragEvent) => {
    const key = e.dataTransfer.getData(DRAG_TYPE)
    if (!key || !onDropItem) return
    e.preventDefault()
    onDropItem(key, flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div
        className="h-[min(560px,64vh)] min-h-[360px] overflow-hidden rounded-card border border-line"
        role="region"
        aria-label={t(label)}
        data-testid="chain-canvas"
        onDragOver={(e) => {
          if (onDropItem && e.dataTransfer.types.includes(DRAG_TYPE)) {
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
          }
        }}
        onDrop={drop}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onSelectionChange={({ nodes: picked }) => onSelect(picked[0]?.id ?? null)}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          edgesFocusable={!readOnly}
          deleteKeyCode={readOnly ? null : ['Delete', 'Backspace']}
          fitView
          fitViewOptions={FIT}
          minZoom={0.15}
          maxZoom={1.6}
          colorMode={theme}
          proOptions={{ hideAttribution: true }}
          style={FLOW_STYLE}
          ariaLabelConfig={{
            'node.a11yDescription.default': t('Enter나 Space로 단계를 고릅니다. Delete로 뺍니다.'),
            'node.a11yDescription.keyboardDisabled': t('Enter나 Space로 단계를 고르고 화살표로 옮깁니다. Delete로 뺍니다.'),
            'edge.a11yDescription.default': t('Enter나 Space로 선을 고르고 Delete로 지웁니다.'),
            'handle.ariaLabel': t('연결점'),
          }}
        >
          <Background gap={20} size={1} />
        </ReactFlow>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <ZoomButton label={t('화면에 맞추기')} onClick={() => void flow.fitView({ ...FIT, duration: 200 })}>
          <Maximize size={15} />
        </ZoomButton>
        <ZoomButton label={t('확대')} onClick={() => void flow.zoomIn()}>
          <ZoomIn size={15} />
        </ZoomButton>
        <ZoomButton label={t('축소')} onClick={() => void flow.zoomOut()}>
          <ZoomOut size={15} />
        </ZoomButton>
        {footer}
      </div>
    </div>
  )
}
