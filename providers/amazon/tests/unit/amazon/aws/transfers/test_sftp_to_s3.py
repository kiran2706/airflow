#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from airflow.models import DAG
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.amazon.aws.transfers.sftp_to_s3 import SFTPToS3Operator
from airflow.providers.ssh.hooks.ssh import SSHHook
from airflow.providers.ssh.operators.ssh import SSHOperator
from airflow.utils.timezone import datetime

from tests_common.test_utils.config import conf_vars

pytestmark = pytest.mark.db_test

BUCKET = "test-bucket"
S3_KEY = "test/test_1_file.csv"
SFTP_PATH = "/tmp/remote_path.txt"
SFTP_CONN_ID = "ssh_default"
S3_CONN_ID = "aws_default"

SFTP_MOCK_FILE = "test_sftp_file.csv"
S3_MOCK_FILES = "test_1_file.csv"

TEST_DAG_ID = "unit_tests_sftp_tos3_op"
DEFAULT_DATE = datetime(2018, 1, 1)


class TestSFTPToS3Operator:
    def setup_method(self):
        hook = SSHHook(ssh_conn_id="ssh_default")

        hook.no_host_key_check = True
        dag = DAG(
            f"{TEST_DAG_ID}test_schedule_dag_once",
            schedule="@once",
            start_date=DEFAULT_DATE,
        )

        self.hook = hook

        self.ssh_client = self.hook.get_conn()
        self.sftp_client = self.ssh_client.open_sftp()

        self.dag = dag
        self.s3_bucket = BUCKET
        self.sftp_path = SFTP_PATH
        self.s3_key = S3_KEY

    @pytest.mark.parametrize("use_temp_file", [True, False])
    @mock_aws
    @conf_vars({("core", "enable_xcom_pickling"): "True"})
    def test_sftp_to_s3_operation(self, use_temp_file):
        # Setting
        test_remote_file_content = (
            "This is remote file content \n which is also multiline "
            "another line here \n this is last line. EOF"
        )

        # create a test file remotely
        create_file_task = SSHOperator(
            task_id="test_create_file",
            ssh_hook=self.hook,
            command=f"echo '{test_remote_file_content}' > {self.sftp_path}",
            do_xcom_push=True,
            dag=self.dag,
        )
        assert create_file_task is not None
        create_file_task.execute(None)

        # Test for creation of s3 bucket
        s3_hook = S3Hook(aws_conn_id=None)
        conn = boto3.client("s3")
        conn.create_bucket(Bucket=self.s3_bucket)
        assert s3_hook.check_for_bucket(self.s3_bucket)

        # get remote file to local
        run_task = SFTPToS3Operator(
            s3_bucket=BUCKET,
            s3_key=S3_KEY,
            sftp_path=SFTP_PATH,
            sftp_conn_id=SFTP_CONN_ID,
            s3_conn_id=S3_CONN_ID,
            use_temp_file=use_temp_file,
            task_id="test_sftp_to_s3",
            dag=self.dag,
        )
        assert run_task is not None

        run_task.execute(None)

        # Check if object was created in s3
        objects_in_dest_bucket = conn.list_objects(Bucket=self.s3_bucket, Prefix=self.s3_key)
        # there should be object found, and there should only be one object found
        assert len(objects_in_dest_bucket["Contents"]) == 1

        # the object found should be consistent with dest_key specified earlier
        assert objects_in_dest_bucket["Contents"][0]["Key"] == self.s3_key

        # Clean up after finishing with test
        conn.delete_object(Bucket=self.s3_bucket, Key=self.s3_key)
        conn.delete_bucket(Bucket=self.s3_bucket)
        assert not s3_hook.check_for_bucket(self.s3_bucket)

    @pytest.mark.parametrize("fail_on_file_not_exist", [True, False])
    @mock_aws
    @conf_vars({("core", "enable_xcom_pickling"): "True"})
    def test_sftp_to_s3_fail_on_file_not_exist(self, fail_on_file_not_exist):
        s3_hook = S3Hook(aws_conn_id=None)
        conn = boto3.client("s3")
        conn.create_bucket(Bucket=self.s3_bucket)
        assert s3_hook.check_for_bucket(self.s3_bucket)

        if fail_on_file_not_exist:
            with pytest.raises(FileNotFoundError):
                SFTPToS3Operator(
                    s3_bucket=self.s3_bucket,
                    s3_key=self.s3_key,
                    sftp_path="/tmp/wrong_path.txt",
                    sftp_conn_id=SFTP_CONN_ID,
                    s3_conn_id=S3_CONN_ID,
                    fail_on_file_not_exist=fail_on_file_not_exist,
                    task_id="test_sftp_to_s3",
                    dag=self.dag,
                ).execute(None)
        else:
            SFTPToS3Operator(
                s3_bucket=self.s3_bucket,
                s3_key=self.s3_key,
                sftp_path=self.sftp_path,
                sftp_conn_id=SFTP_CONN_ID,
                s3_conn_id=S3_CONN_ID,
                fail_on_file_not_exist=fail_on_file_not_exist,
                task_id="test_sftp_to_s3",
                dag=self.dag,
            ).execute(None)

        conn.delete_object(Bucket=self.s3_bucket, Key=self.s3_key)
        conn.delete_bucket(Bucket=self.s3_bucket)
        assert not s3_hook.check_for_bucket(self.s3_bucket)
