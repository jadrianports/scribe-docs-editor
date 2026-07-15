import { useEffect, useReducer } from 'react'
import type { ReactNode } from 'react'
import type { Editor } from '@tiptap/react'

// A small formatting toolbar. It force-re-renders on every editor transaction so
// the active-state highlighting stays in sync with the cursor/selection.
export function Toolbar({ editor }: { editor: Editor }) {
  const [, force] = useReducer((x: number) => x + 1, 0)

  useEffect(() => {
    const update = () => force()
    editor.on('transaction', update)
    return () => {
      editor.off('transaction', update)
    }
  }, [editor])

  const Button = ({
    active,
    onClick,
    title,
    children,
  }: {
    active: boolean
    onClick: () => void
    title: string
    children: ReactNode
  }) => (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={
        'min-w-8 rounded px-2 py-1 text-sm transition ' +
        (active
          ? 'bg-slate-800 text-white'
          : 'text-slate-700 hover:bg-slate-200')
      }
    >
      {children}
    </button>
  )

  const Divider = () => <span className="mx-1 w-px self-stretch bg-slate-300" />

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b border-slate-200 bg-white/90 px-3 py-2 backdrop-blur">
      <Button active={editor.isActive('bold')} title="Bold (Ctrl+B)" onClick={() => editor.chain().focus().toggleBold().run()}>
        <span className="font-bold">B</span>
      </Button>
      <Button active={editor.isActive('italic')} title="Italic (Ctrl+I)" onClick={() => editor.chain().focus().toggleItalic().run()}>
        <span className="italic">I</span>
      </Button>
      <Button active={editor.isActive('underline')} title="Underline (Ctrl+U)" onClick={() => editor.chain().focus().toggleUnderline().run()}>
        <span className="underline">U</span>
      </Button>
      <Divider />
      <Button active={editor.isActive('paragraph')} title="Body text" onClick={() => editor.chain().focus().setParagraph().run()}>
        P
      </Button>
      <Button active={editor.isActive('heading', { level: 1 })} title="Heading 1" onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
        H1
      </Button>
      <Button active={editor.isActive('heading', { level: 2 })} title="Heading 2" onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
        H2
      </Button>
      <Button active={editor.isActive('heading', { level: 3 })} title="Heading 3" onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}>
        H3
      </Button>
      <Divider />
      <Button active={editor.isActive('bulletList')} title="Bulleted list" onClick={() => editor.chain().focus().toggleBulletList().run()}>
        • List
      </Button>
      <Button active={editor.isActive('orderedList')} title="Numbered list" onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        1. List
      </Button>
    </div>
  )
}
