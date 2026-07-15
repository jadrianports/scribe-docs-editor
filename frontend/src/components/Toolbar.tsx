import { useEffect, useReducer } from 'react'
import type { ReactNode } from 'react'
import type { Editor } from '@tiptap/react'

// A single toolbar button. `onMouseDown` preventDefault is essential: without it,
// pressing the button moves focus out of the contenteditable and collapses the
// editor selection *before* onClick runs, so the formatting command would apply
// to nothing. Preventing the default mousedown keeps focus (and the selection)
// in the editor while the click still fires.
function ToolbarButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean
  onClick: () => void
  title: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={
        'min-w-8 rounded px-2 py-1 text-sm transition ' +
        (active ? 'bg-slate-800 text-white' : 'text-slate-700 hover:bg-slate-200')
      }
    >
      {children}
    </button>
  )
}

function Divider() {
  return <span className="mx-1 w-px self-stretch bg-slate-300" />
}

// The toolbar force-re-renders on every editor transaction so the active-state
// highlighting stays in sync with the cursor/selection.
export function Toolbar({ editor }: { editor: Editor }) {
  const [, force] = useReducer((x: number) => x + 1, 0)

  useEffect(() => {
    const update = () => force()
    editor.on('transaction', update)
    return () => {
      editor.off('transaction', update)
    }
  }, [editor])

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b border-slate-200 bg-white/90 px-3 py-2 backdrop-blur">
      <ToolbarButton active={editor.isActive('bold')} title="Bold (Ctrl+B)" onClick={() => editor.chain().focus().toggleBold().run()}>
        <span className="font-bold">B</span>
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('italic')} title="Italic (Ctrl+I)" onClick={() => editor.chain().focus().toggleItalic().run()}>
        <span className="italic">I</span>
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('underline')} title="Underline (Ctrl+U)" onClick={() => editor.chain().focus().toggleUnderline().run()}>
        <span className="underline">U</span>
      </ToolbarButton>
      <Divider />
      <ToolbarButton active={editor.isActive('paragraph')} title="Body text" onClick={() => editor.chain().focus().setParagraph().run()}>
        P
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('heading', { level: 1 })} title="Heading 1" onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
        H1
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('heading', { level: 2 })} title="Heading 2" onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
        H2
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('heading', { level: 3 })} title="Heading 3" onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
        H3
      </ToolbarButton>
      <Divider />
      <ToolbarButton active={editor.isActive('bulletList')} title="Bulleted list" onClick={() => editor.chain().focus().toggleBulletList().run()}>
        • List
      </ToolbarButton>
      <ToolbarButton active={editor.isActive('orderedList')} title="Numbered list" onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        1. List
      </ToolbarButton>
    </div>
  )
}
