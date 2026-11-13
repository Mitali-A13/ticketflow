from app.ratelimit import RateLimiter


def make(limit=2, window=60.0):
    t = [0.0]
    return RateLimiter(limit, window, clock=lambda: t[0]), t


def test_allows_up_to_the_limit():
    rl, _ = make()
    assert rl.check("k") == (True, 0)
    assert rl.check("k") == (True, 0)


def test_blocks_over_the_limit_with_retry_after():
    rl, t = make()
    rl.check("k")
    t[0] = 10.0
    rl.check("k")
    t[0] = 20.0
    assert rl.check("k") == (False, 40)  # the oldest hit (t=0) expires at t=60


def test_window_slides():
    rl, t = make()
    rl.check("k")
    rl.check("k")
    t[0] = 20.0
    assert rl.check("k")[0] is False
    t[0] = 60.5  # both early hits have aged out
    assert rl.check("k")[0] is True


def test_keys_are_separate():
    rl, _ = make(limit=1)
    assert rl.check("a")[0] is True
    assert rl.check("a")[0] is False
    assert rl.check("b")[0] is True
