package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.BUILD_MANIFEST_QUEUE;

import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusType;
import edu.washu.tag.extractor.hl7log.db.FileStatus;
import edu.washu.tag.extractor.hl7log.db.IngestDbService;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7FilesOutput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbInput;
import edu.washu.tag.extractor.hl7log.model.WriteHl7FilesErrorStatusToDbOutput;
import edu.washu.tag.extractor.hl7log.util.FileHandler;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = BUILD_MANIFEST_QUEUE)
public class FindHl7FilesImpl implements FindHl7Files {
    private static final Logger logger = Workflow.getLogger(FindHl7FilesImpl.class);

    private final FileHandler fileHandler;
    private final IngestDbService ingestDbService;

    public FindHl7FilesImpl(FileHandler fileHandler, IngestDbService ingestDbService) {
        this.fileHandler = fileHandler;
        this.ingestDbService = ingestDbService;
    }

    @Override
    public FindHl7FilesOutput findHl7FilesAndWriteManifest(FindHl7FilesInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Finding HL7 files at root path {}",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.hl7RootPath());

        List<String> hl7Files = fileHandler.ls(URI.create(input.hl7RootPath()));

        if (hl7Files.isEmpty()) {
            throw ApplicationFailure.newFailure("No HL7 files found", "type");
        }

        String manifestFilePath = input.scratchDir() + "/hl7_manifest.txt";
        logger.info("WorkflowId {} ActivityId {} - Writing {} HL7 file paths to manifest file {}",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7Files.size(), manifestFilePath);
        try {
            String contents = String.join("\n", hl7Files);
            fileHandler.putWithRetry(contents.getBytes(StandardCharsets.UTF_8), URI.create(manifestFilePath));
            return new FindHl7FilesOutput(manifestFilePath);
        } catch (IOException e) {
            throw ApplicationFailure.newFailureWithCause("Error writing HL7 manifest file", "type", e);
        }
    }

    @Override
    public WriteHl7FilesErrorStatusToDbOutput writeHl7FilesErrorStatusToDb(WriteHl7FilesErrorStatusToDbInput input) {
        ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        logger.info("WorkflowId {} ActivityId {} - Reading HL7 file manifest {}",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), input.manifestFilePath());

        URI manifestFilePath = URI.create(input.manifestFilePath());
        List<String> hl7Paths;
        try {
            byte[] manifestFileBytes = fileHandler.read(manifestFilePath);
            hl7Paths = new String(manifestFileBytes, StandardCharsets.UTF_8).lines().toList();
        } catch (IOException e) {
            logger.error("Error reading HL7 file manifest", e);
            throw ApplicationFailure.newFailureWithCause("Error reading HL7 file manifest", "type", e);
        }
        logger.info("WorkflowId {} ActivityId {} - Found {} HL7 file paths in manifest file",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7Paths.size());

        List<FileStatus> fileStatuses = hl7Paths.stream()
            .map(path -> FileStatus.failed(
                path, FileStatusType.HL7, input.errorMessage(), activityInfo.getWorkflowId(), activityInfo.getActivityId()
            ))
            .toList();

        logger.info("WorkflowId {} ActivityId {} - Writing {} HL7 file error statuses to database",
            activityInfo.getWorkflowId(), activityInfo.getActivityId(), fileStatuses.size());
        try {
            ingestDbService.batchInsertFileStatuses(fileStatuses);
        } catch (Exception e) {
            logger.error("Error writing HL7 file error status to database", e);
            throw ApplicationFailure.newFailureWithCause("Error writing HL7 file error status to database", "type", e);
        }
        return null;
    }
}
