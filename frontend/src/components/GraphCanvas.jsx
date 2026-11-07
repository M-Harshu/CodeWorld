import React, { useEffect, useMemo } from 'react'
import ReactFlow, { Background, Controls, MiniMap, MarkerType, ReactFlowProvider, useReactFlow, Handle, Position } from 'reactflow'
import 'reactflow/dist/style.css'
import dagre from 'dagre'

const PALETTE = ['#2B8A8A', '#5B9BD5', '#C4476B', '#6FA287', '#E0A526', '#8C6BB1', '#D2691E', '#4F7CAC']
export const colorFor = (key = '') => { let h = 0; for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length] }
const ROLE = { seed: '#C4476B', direct: '#E0A526', indirect: '#6FA287', test: '#5B9BD5' }

function CWNode({ data }) {
  const color = data.role ? ROLE[data.role] : colorFor(data.top)
  return (
    <div className={`cw-node ${data.kind}${data.selected ? ' sel' : ''}${data.highlight ? ' hl' : ''}`} style={{ borderLeft: `5px solid ${color}` }}
      onDoubleClick={() => data.onToggle && data.onToggle(data)}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="k"><span>{data.role || data.kind}</span>{data.loc ? <span>{data.loc} loc</span> : null}</div>
      <div className="n">{data.label}</div>
      {data.children > 0 && <div className="exp" onClick={(e) => { e.stopPropagation(); data.onToggle && data.onToggle(data) }}>{data.expanded ? '▾ collapse' : `▸ ${data.children} inside`}</div>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
}
const nodeTypes = { cw: CWNode }

function layout(nodes, edges, dir) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: dir, nodesep: 26, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))
  nodes.forEach((n) => g.setNode(n.id, { width: 200, height: n.data.children > 0 ? 66 : 54 }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map((n) => { const p = g.node(n.id); return { ...n, position: { x: p.x - 100, y: p.y - 30 } } })
}

const EDGE = { CALLS: { stroke: '#2B8A8A' }, IMPORTS: { stroke: '#8CA3AD', strokeDasharray: '5 4' }, INHERITS: { stroke: '#C4476B' } }

function Inner({ nodes, edges, onSelect, onToggle, selected, dir, minimap }) {
  const rf = useReactFlow()
  const { rfNodes, rfEdges } = useMemo(() => {
    const ids = new Set(nodes.map((n) => n.id))
    const rn = nodes.map((n) => ({ id: n.id, type: 'cw', position: { x: 0, y: 0 }, data: { ...n, selected: n.id === selected, onToggle } }))
    const re = edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e, i) => ({
      id: `${e.source}>${e.target}>${e.type}`, source: e.source, target: e.target, type: 'default',
      label: e.weight > 1 ? String(e.weight) : undefined, labelStyle: { fontSize: 10, fill: '#697F8C' }, labelBgStyle: { fill: 'transparent' },
      style: { ...(EDGE[e.type] || EDGE.CALLS), strokeWidth: 1.2 + Math.min(1.4, Math.log2(e.weight || 1) * 0.35) },
      markerEnd: { type: MarkerType.ArrowClosed, color: (EDGE[e.type] || EDGE.CALLS).stroke, width: 11, height: 11 },
    }))
    return { rfNodes: layout(rn, re, dir), rfEdges: re }
  }, [nodes, edges, dir, selected, onToggle])
  const sig = nodes.map((n) => n.id).join('|')
  useEffect(() => { const t = setTimeout(() => rf.fitView({ padding: 0.18, duration: 250, maxZoom: 1.1 }), 60); return () => clearTimeout(t) }, [sig])
  return (
    <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} minZoom={0.1} maxZoom={2} nodesDraggable nodesConnectable={false}
      onNodeClick={(_, n) => onSelect && onSelect(n.data)} proOptions={{ hideAttribution: true }} fitView>
      <Background gap={28} color="rgba(43,138,138,.12)" />
      <Controls showInteractive={false} />
      {minimap && <MiniMap pannable zoomable nodeColor={(n) => (n.data.role ? ROLE[n.data.role] : colorFor(n.data.top))} />}
    </ReactFlow>
  )
}

export default function GraphCanvas(props) {
  return <ReactFlowProvider><Inner dir="LR" minimap {...props} /></ReactFlowProvider>
}
