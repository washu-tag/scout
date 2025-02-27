package edu.washu.tag.temporal.util;

import io.temporal.failure.TemporalFailure;
import io.temporal.workflow.Promise;

import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;

public class WorkflowUtils {

    /**
     * Collect the results of a list of promises, waiting for each to complete.
     * If one of the activities has failed with a TemporalFailure, it will be logged and ignored.
     * If one of the activities has failed with another exception, it will be rethrown.
     * @param promises List of promises to collect results from
     * @return List of results from the promises that succeeded
     * @param <T> Type of the results
     * @throws RuntimeException If one of the activities failed with an exception other than TemporalFailure
     */
    public static <T> List<T> getSuccessfulResults(Deque<Promise<T>> promises, Logger logger) throws RuntimeException {
        // Collect async results
        List<T> results = new ArrayList<>(promises.size());
        while (!promises.isEmpty()) {
            Promise<T> promise = promises.poll();
            try {
                results.add(promise.get(10, TimeUnit.MILLISECONDS));
            } catch (TimeoutException ignored) {
                // This is benign, it just means the activity hasn't completed yet
                // Back to the queue
                promises.add(promise);
            } catch (TemporalFailure exception) {
                logger.warn("An activity failed, but we ignore it. The workflow continues.", exception);
            } catch (Exception exception) {
                logger.error("An activity failed and the workflow will fail too.", exception);
                throw exception;
            }
        }
        return results;
    }
}
