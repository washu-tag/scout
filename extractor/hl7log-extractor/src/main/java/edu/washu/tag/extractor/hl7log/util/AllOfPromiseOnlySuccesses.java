package edu.washu.tag.extractor.hl7log.util;

import io.temporal.workflow.CompletablePromise;
import io.temporal.workflow.Functions;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;

/**
 * Copy of {@link io.temporal.internal.sync.AllOfPromise AllOfPromise}
 * except that it ignores failures.
 * Completes when all promises are either successful or failed.
 */
public class AllOfPromiseOnlySuccesses<T> implements Promise<List<T>> {
    private static final Logger logger = Workflow.getLogger(AllOfPromiseOnlySuccesses.class);

    private final CompletablePromise<List<T>> impl = Workflow.newPromise();
    private final AtomicInteger notReadyCount = new AtomicInteger(0);

    private final List<T> results;

    AllOfPromiseOnlySuccesses(Promise<T>[] promises) {
        results = new ArrayList<>(promises.length);

        for (Promise<T> f : promises) {
            this.addPromise(f);
        }

        if (this.notReadyCount.get() == 0) {
            this.impl.complete(results);
        }

    }

    public AllOfPromiseOnlySuccesses(Iterable<Promise<T>> promises) {
        results = new ArrayList<>();
        for (Promise<T> f : promises) {
            this.addPromise(f);
        }

        if (this.notReadyCount.get() == 0) {
            this.impl.complete(results);
        }

    }

    private void addPromise(Promise<T> f) {
        if (!f.isCompleted()) {
            this.notReadyCount.incrementAndGet();
            f.handle((r, e) -> {
                if (this.notReadyCount.get() == 0) {
                    throw new Error("Unexpected 0 count");
                } else if (this.impl.isCompleted()) {
                    return null;
                } else {
                    if (e != null) {
                        logger.warn("Promise {} failed", f, e);
                    } else {
                        logger.debug("Promise {} succeeded", f);
                        this.results.add(r);
                    }

                    if (this.notReadyCount.decrementAndGet() == 0) {
                        this.impl.complete(this.results);
                    }

                    return null;
                }
            });
        } else {
            this.results.add(f.get());
        }

    }

    public boolean isCompleted() {
        return this.impl.isCompleted();
    }

    public List<T> get() {
        return this.impl.get();
    }

    public List<T> cancellableGet() {
        return this.impl.cancellableGet();
    }

    public List<T> cancellableGet(long timeout, TimeUnit unit) throws TimeoutException {
        return this.impl.cancellableGet(timeout, unit);
    }

    public List<T> get(long timeout, TimeUnit unit) throws TimeoutException {
        return this.impl.get(timeout, unit);
    }

    public RuntimeException getFailure() {
        return this.impl.getFailure();
    }

    public <U> Promise<U> thenApply(Functions.Func1<? super List<T>, ? extends U> fn) {
        return this.impl.thenApply(fn);
    }

    public <U> Promise<U> handle(Functions.Func2<? super List<T>, RuntimeException, ? extends U> fn) {
        return this.impl.handle(fn);
    }

    public <U> Promise<U> thenCompose(Functions.Func1<? super List<T>, ? extends Promise<U>> fn) {
        return this.impl.thenCompose(fn);
    }

    public Promise<List<T>> exceptionally(Functions.Func1<Throwable, ? extends List<T>> fn) {
        return this.impl.exceptionally(fn);
    }
}