/**
 * Toolbar.tsx:44-52's `useReducer` + `editor.on('transaction')` subscription is
 * load-bearing (D-14): `@tiptap/react` 3.27.4 defaults `shouldRerenderOnTransaction`
 * to the no-rerender branch, so without this subscription formatting still applies
 * to the document but the toolbar buttons silently freeze -- a regression that would
 * otherwise pass every functional (non-UI) test. This file covers active-mark state
 * against a REAL TipTap editor (D-10) with a named regression test for that
 * subscription, plus click->command wiring against a stub editor (Task 2 below).
 */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { Toolbar } from './Toolbar'

// Mirrors EditorPage.tsx's useEditor StarterKit config (same disables) so the
// schema this real editor exercises matches production, per D-10.
const FIXTURE_HTML = '<p>plain <strong>bold</strong> text</p>'

function buildRealEditor() {
  return new Editor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        link: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        undoRedo: false,
      }),
    ],
    content: FIXTURE_HTML,
  })
}

// Finds the position range of the first text node matching `matcher`, so
// selection tests don't hardcode ProseMirror positions against the fixture's
// exact character offsets.
function findTextRange(
  editor: Editor,
  matcher: (text: string, marks: readonly { type: { name: string } }[]) => boolean,
): { from: number; to: number } {
  let range: { from: number; to: number } | undefined
  editor.state.doc.descendants((node, pos) => {
    if (range) return false
    if (node.isText && node.text !== undefined && matcher(node.text, node.marks)) {
      range = { from: pos, to: pos + node.nodeSize }
    }
    return true
  })
  if (!range) {
    throw new Error('findTextRange: no matching text node in fixture')
  }
  return range
}

function midpoint(range: { from: number; to: number }) {
  return Math.floor((range.from + range.to) / 2)
}

// A self-chaining stub matching every command's `.chain().focus()....run()`
// shape (Toolbar.tsx:57-85) with a spy per link so click->command wiring can
// be asserted without a real editor (D-10). `isActive` always returns false
// -- this describe block is about which commands a click invokes, not about
// active-mark rendering (already covered above with a real editor).
function createStubEditor() {
  const calls = {
    focus: vi.fn(),
    toggleBold: vi.fn(),
    toggleHeading: vi.fn(),
    toggleBulletList: vi.fn(),
    run: vi.fn(),
  }

  const chain = {
    focus: (...args: unknown[]) => {
      calls.focus(...args)
      return chain
    },
    toggleBold: (...args: unknown[]) => {
      calls.toggleBold(...args)
      return chain
    },
    toggleHeading: (...args: unknown[]) => {
      calls.toggleHeading(...args)
      return chain
    },
    toggleBulletList: (...args: unknown[]) => {
      calls.toggleBulletList(...args)
      return chain
    },
    run: () => {
      calls.run()
      return true
    },
  }

  const editor = {
    isActive: vi.fn(() => false),
    chain: vi.fn(() => chain),
    on: vi.fn(),
    off: vi.fn(),
  }

  return { editor: editor as unknown as Editor, calls }
}

describe('Toolbar', () => {
  describe('active-mark state (real editor, D-10)', () => {
    let editor: Editor
    let unmount: () => void

    beforeEach(() => {
      editor = buildRealEditor()
      ;({ unmount } = render(<Toolbar editor={editor} />))
    })

    afterEach(() => {
      unmount()
      editor.destroy()
    })

    it('reflects active marks from the current selection', () => {
      const boldRange = findTextRange(editor, (_text, marks) => marks.some((m) => m.type.name === 'bold'))
      const plainRange = findTextRange(editor, (text) => text === 'plain ')
      const boldButton = screen.getByTitle('Bold (Ctrl+B)')

      // Starting selection (doc start) is not inside the bold run.
      expect(boldButton).toHaveAttribute('aria-pressed', 'false')

      // Moving the selection into the bold text is the case that only passes
      // because Toolbar's transaction subscription re-renders on the resulting
      // selection-change transaction -- without it this assertion would read
      // the stale pre-render aria-pressed value.
      act(() => {
        editor.commands.setTextSelection(midpoint(boldRange))
      })
      expect(boldButton).toHaveAttribute('aria-pressed', 'true')

      act(() => {
        editor.commands.setTextSelection(midpoint(plainRange))
      })
      expect(boldButton).toHaveAttribute('aria-pressed', 'false')
    })

    // NAMED regression test guarding Toolbar.tsx:44-52's `editor.on('transaction')`
    // subscription (D-14). `@tiptap/react` 3.27.4 defaults `shouldRerenderOnTransaction`
    // to the no-rerender branch, so this subscription is what keeps active-mark
    // highlighting live -- remove it and `toggleBold()` below still marks the text
    // bold (formatting applies) but the button's `aria-pressed` DOM attribute would
    // stay stuck at 'false' (the button silently freezes).
    //
    // MUTATION-VALIDATED (D-14, discretionary extension of D-11/D-24 per Claude's
    // Discretion in 09-CONTEXT.md): temporarily deleted the `useEffect` block at
    // Toolbar.tsx:47-53 (the `editor.on('transaction', update)` subscription and its
    // cleanup) and re-ran `npm test -- Toolbar`. Both this test and the one above
    // went RED -- `aria-pressed` stayed at 'false' after the transaction because
    // Toolbar never re-rendered. Restored Toolbar.tsx verbatim afterward (`git diff`
    // confirmed empty) and re-ran the suite green. This test's discriminating power
    // is proven, not assumed.
    it('keeps active-mark highlighting live via the transaction subscription (D-14 regression)', () => {
      const plainRange = findTextRange(editor, (text) => text === 'plain ')
      const boldButton = screen.getByTitle('Bold (Ctrl+B)')

      act(() => {
        // Select the whole "plain" word (not the trailing space) so toggleBold()
        // below has a non-collapsed range to mark, without touching the existing
        // bold run.
        editor.commands.setTextSelection({ from: plainRange.from, to: plainRange.from + 5 })
      })
      expect(boldButton).toHaveAttribute('aria-pressed', 'false')

      act(() => {
        editor.commands.toggleBold()
      })
      // Formatting applied (the mark exists in the doc) AND the toolbar's DOM
      // reflects it -- the second half is what the transaction subscription buys.
      expect(editor.isActive('bold')).toBe(true)
      expect(boldButton).toHaveAttribute('aria-pressed', 'true')
    })
  })

  describe('click->command wiring (stub editor, D-10/D-15)', () => {
    // Subset choice (D-15 discretion): a representative button per distinct
    // command shape rather than all ten -- a mark toggle with no arguments
    // (Bold), a heading toggle whose `{ level }` argument must thread through
    // correctly (Heading 2), and a list toggle with no arguments (Bulleted
    // list). Italic/Underline/Paragraph/H1/H3/Numbered-list follow the exact
    // same two shapes already exercised here.
    it('invokes chain().focus().toggleBold().run() when the Bold button is clicked (mark toggle)', async () => {
      const { editor, calls } = createStubEditor()
      const { unmount } = render(<Toolbar editor={editor} />)
      const user = userEvent.setup()

      await user.click(screen.getByTitle('Bold (Ctrl+B)'))

      expect(calls.focus).toHaveBeenCalledTimes(1)
      expect(calls.toggleBold).toHaveBeenCalledTimes(1)
      expect(calls.run).toHaveBeenCalledTimes(1)
      unmount()
    })

    it('passes { level: 2 } through to toggleHeading when Heading 2 is clicked (heading-with-level)', async () => {
      const { editor, calls } = createStubEditor()
      const { unmount } = render(<Toolbar editor={editor} />)
      const user = userEvent.setup()

      await user.click(screen.getByTitle('Heading 2'))

      expect(calls.toggleHeading).toHaveBeenCalledWith({ level: 2 })
      expect(calls.run).toHaveBeenCalledTimes(1)
      unmount()
    })

    it('invokes chain().focus().toggleBulletList().run() when the Bulleted list button is clicked (list toggle)', async () => {
      const { editor, calls } = createStubEditor()
      const { unmount } = render(<Toolbar editor={editor} />)
      const user = userEvent.setup()

      await user.click(screen.getByTitle('Bulleted list'))

      expect(calls.focus).toHaveBeenCalledTimes(1)
      expect(calls.toggleBulletList).toHaveBeenCalledTimes(1)
      expect(calls.run).toHaveBeenCalledTimes(1)
      unmount()
    })
  })
})
