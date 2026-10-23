// Keep upstream's draft-storage calls in memory in authenticated server mode.
// Configuration (possibly containing API keys) must not persist in localStorage.
const localStorage = (() => {
    const values = new Map();
    return {getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key)};
})();
