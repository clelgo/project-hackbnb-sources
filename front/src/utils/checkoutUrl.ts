const STRIPE_CHECKOUT_HOST = "checkout.stripe.com"

/**
 * Only allow HTTPS URLs pointing to Stripe Checkout.
 *
 * The URL comes from the API and must never be trusted blindly: opening an
 * arbitrary scheme/host with window.open enables phishing, tabnabbing and
 * javascript:/data: URL injection.
 */
export const isAllowedStripeCheckoutUrl = (value: unknown): value is string => {
    if (typeof value !== "string") {
        return false
    }

    let url: URL
    try {
        url = new URL(value)
    } catch {
        return false
    }

    return (
        url.protocol === "https:" &&
        url.hostname === STRIPE_CHECKOUT_HOST &&
        url.port === "" &&
        url.username === "" &&
        url.password === ""
    )
}
