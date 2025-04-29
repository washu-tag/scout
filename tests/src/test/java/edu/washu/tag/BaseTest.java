package edu.washu.tag;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class BaseTest {

    private static final Logger log = LoggerFactory.getLogger(BaseTest.class);
    protected TestConfig config;

    public BaseTest() {
        config = TestConfig.instance;
    }

}
