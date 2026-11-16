import { createContext } from 'react'

import type { DatabaseId } from '@/api/explorer'

/**
 * The database the conversation on screen is about (U7.1b), for parts of a cell that link to the
 * explorer ("see this answer's tables"): a Sales answer's tables are Sales's, whatever the
 * switcher said a moment ago. Null = the demo.
 */
export const ConversationDatabase = createContext<DatabaseId>(null)
