import type { Role } from '../api'

// Central mapping from a document role to what the UI allows and shows.
// Kept as pure functions so the access rules are trivially unit-testable.

export function canEdit(role: Role): boolean {
  return role === 'owner' || role === 'editor'
}

export function isOwner(role: Role): boolean {
  return role === 'owner'
}

export function roleBadge(role: Role): string {
  switch (role) {
    case 'owner':
      return 'Owner'
    case 'editor':
      return 'Can edit'
    case 'viewer':
      return 'View only'
    default:
      return role
  }
}
