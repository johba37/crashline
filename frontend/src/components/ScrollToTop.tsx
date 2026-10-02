import { useLayoutEffect } from 'react'
import { useLocation } from 'react-router'

/** A new page starts at its top. Only the path counts: the app's choices live in the query string. */
export default function ScrollToTop() {
  const { pathname, hash } = useLocation()
  useLayoutEffect(() => {
    if (!hash) window.scrollTo({ top: 0, behavior: 'instant' })
  }, [pathname, hash])
  return null
}
