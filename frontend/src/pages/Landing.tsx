import { useRef } from 'react'
import { Link } from 'react-router'
import HeroArt from '../components/HeroArt.tsx'
import CheckEveryPrice from '../components/landing/CheckEveryPrice.tsx'
import Footer from '../components/landing/Footer.tsx'
import HowItWorks from '../components/landing/HowItWorks.tsx'
import RisksAndFees from '../components/landing/RisksAndFees.tsx'
import WhatsNext from '../components/landing/WhatsNext.tsx'
import NavBar from '../components/NavBar.tsx'
import Starfield from '../components/Starfield.tsx'

// Eased rather than linear, so the fade has no visible start or end band.
const FADE_TO_PAGE = `linear-gradient(to bottom, transparent,
  color-mix(in oklab, var(--page-surface) 8%, transparent) 25%,
  color-mix(in oklab, var(--page-surface) 25%, transparent) 45%,
  color-mix(in oklab, var(--page-surface) 55%, transparent) 65%,
  color-mix(in oklab, var(--page-surface) 85%, transparent) 85%,
  var(--page-surface))`

export default function Landing() {
  const heroCta = useRef<HTMLAnchorElement>(null)

  return (
    <>
      <NavBar revealAfter={heroCta} />
      <main id="top">
        {/* The hero is a night scene, so it stays dark in both themes. Phones: text above the art;
            desktop: text next to the art, which is anchored to the right edge of the screen, or on
            screens wider than 1800px to the right edge of a centred 1800px stage, so it stays next
            to the text. */}
        <section data-theme="dark" className="relative isolate overflow-hidden bg-surface pt-6 text-ink lg:pt-12 lg:pb-12">
          <Starfield className="pointer-events-none absolute inset-0 -z-10 size-full" />
          {/* Art height (0.43 x its width) plus room above and below. The art stops growing at
              1100px (1719px viewports, i.e. 24-inch monitors and up), so the height does too. */}
          <div className="relative lg:flex lg:min-h-[calc(min(27.5vw,472px)+8rem)] lg:items-center">
            <div className="relative z-10 mx-auto w-full max-w-landing px-4 pt-12 sm:px-6 lg:px-8 lg:py-16">
              <div className="max-w-[85%] sm:max-w-md lg:max-w-none">
                {/* Stacked, one phrase per line. The widest line is 7.7em in Syne 800; the art leaves
                    about 10.6em of room next to it, and phones 11.2em at the 34px minimum. */}
                <h1 className="type-display-xl text-ink">
                  <span className="block">Crash</span>
                  <span className="block">insurance</span>
                  <span className="block">that pays.</span>
                </h1>
                <p className="mt-5 max-w-md type-body-lg text-ink-muted">
                  Protect your tokenized stocks from market crashes with a decentralized, model-driven insurance protocol.
                </p>
                <Link
                  ref={heroCta}
                  to="/app"
                  className="mt-8 inline-flex h-12 items-center rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed"
                >
                  Open the app
                </Link>
              </div>
            </div>
            <HeroArt className="-mt-[39vw] md:mt-6 lg:absolute lg:top-1/2 lg:right-[max(0px,(100%_-_1800px)/2)] lg:mt-0 lg:w-[min(64vw,1100px)] lg:-translate-y-1/2" />
          </div>
          {/* Fade the scene into the page below: stars dissolve into black in the dark theme,
              space turns into the silver ground in the light one. Above the art, below the text. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 z-[5] h-40 lg:h-48"
            style={{ background: FADE_TO_PAGE }}
          />
        </section>

        {/* Section ids are the nav's anchors (NavBar.tsx). */}
        <HowItWorks />
        <CheckEveryPrice />
        <RisksAndFees />
        <WhatsNext />
      </main>
      <Footer />
      {/* Film grain over the whole page: the retro finish. */}
      <div aria-hidden="true" className="film-grain" />
    </>
  )
}
