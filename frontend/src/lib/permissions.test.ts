import { describe, expect, it } from 'vitest'
import { canEdit, isOwner, roleBadge } from './permissions'

describe('permissions', () => {
  it('lets owners and editors edit, but not viewers', () => {
    expect(canEdit('owner')).toBe(true)
    expect(canEdit('editor')).toBe(true)
    expect(canEdit('viewer')).toBe(false)
  })

  it('identifies the owner role only', () => {
    expect(isOwner('owner')).toBe(true)
    expect(isOwner('editor')).toBe(false)
    expect(isOwner('viewer')).toBe(false)
  })

  it('maps roles to human-readable badges', () => {
    expect(roleBadge('owner')).toBe('Owner')
    expect(roleBadge('editor')).toBe('Can edit')
    expect(roleBadge('viewer')).toBe('View only')
  })
})
