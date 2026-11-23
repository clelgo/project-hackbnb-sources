import assert from "node:assert/strict"
import test from "node:test"

import { isAllowedStripeCheckoutUrl } from "../src/utils/checkoutUrl.ts"

test("accepts Stripe Checkout HTTPS URLs", () => {
    assert.equal(
        isAllowedStripeCheckoutUrl("https://checkout.stripe.com/c/pay/cs_test_123"),
        true,
    )
    assert.equal(
        isAllowedStripeCheckoutUrl("https://CHECKOUT.STRIPE.COM/c/pay/cs_test_123"),
        true,
    )
})

test("rejects URLs that do not target Stripe Checkout over HTTPS", () => {
    const rejectedValues: unknown[] = [
        // Other hosts and look-alike domains
        "https://evil.example/c/pay/cs_test_123",
        "https://checkout.stripe.com.evil.example/c/pay/cs_test_123",
        "https://checkout.stripe.com@evil.example/c/pay/cs_test_123",
        "https://checkout.stripe.com./c/pay/cs_test_123",
        // Dangerous schemes
        "javascript:alert(document.domain)",
        "data:text/html,<script>alert(document.domain)</script>",
        "http://checkout.stripe.com/c/pay/cs_test_123",
        // Relative or malformed URLs
        "//checkout.stripe.com/c/pay/cs_test_123",
        "/booking/ABC",
        "",
        // Non-default port or embedded credentials
        "https://checkout.stripe.com:8443/c/pay/cs_test_123",
        "https://user:password@checkout.stripe.com/c/pay/cs_test_123",
        // Non-string API values
        null,
        undefined,
        123,
        {},
    ]

    for (const value of rejectedValues) {
        assert.equal(
            isAllowedStripeCheckoutUrl(value),
            false,
            `expected URL to be rejected: ${String(value)}`,
        )
    }
})
