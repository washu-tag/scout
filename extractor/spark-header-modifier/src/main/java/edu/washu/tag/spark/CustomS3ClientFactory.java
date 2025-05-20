package edu.washu.tag.spark;

import com.amazonaws.services.s3.AmazonS3;
import org.apache.hadoop.fs.s3a.S3AFileSystem;

public class CustomS3AFileSystem extends S3AFileSystem {


    @Override
    protected void setAmazonS3Client(AmazonS3 client) {
        super.setAmazonS3Client(client);
    }
}
