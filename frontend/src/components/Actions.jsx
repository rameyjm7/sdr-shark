import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Box, Typography, Button } from '@mui/material';
import TaskForm from './sdr_scheduler/TaskForm';
import TaskList from './sdr_scheduler/TaskList';

const Actions = ({ tasks, setTasks, settings, setSettings, duplicateTask, deleteTask, currentTaskIndex }) => {

  // Function to add a new task
  const handleAddTask = async (newTask) => {
    try {
      const response = await axios.post('/actions/tasks', newTask, {
        headers: {
          'Content-Type': 'application/json',
        },
      });
      setTasks((prevTasks) => [...prevTasks, response.data]);
    } catch (error) {
      console.error('Error adding task:', error);
    }
  };

  // Function to handle drag-and-drop reordering
  const onDragEnd = (result) => {
    if (!result.destination) return;

    const reorderedTasks = Array.from(tasks);
    const [removed] = reorderedTasks.splice(result.source.index, 1);
    reorderedTasks.splice(result.destination.index, 0, removed);

    setTasks(reorderedTasks);
  };


  // Fetch tasks from the server
  useEffect(() => {
    const fetchTasks = async () => {
      try {
        const response = await axios.get('/actions/tasks');
        setTasks(response.data || []);
      } catch (error) {
        console.error('Error fetching tasks:', error);
        setTasks([]);
      }
    };

    // fetchTasks();
  }, [setTasks]);

  const handleExecuteTasks = async () => {
    try {
      const response = await axios.post('/actions/tasks/execute', {}, {
        headers: {
          'Content-Type': 'application/json',
        },
      });
      console.log('Tasks executed:', response.data);
    } catch (error) {
      console.error('Error executing tasks:', error);
    }
    setSettings(settings);
  };

  return (
    <Box>
      <Typography variant="h4">Actions</Typography>
      <TaskForm addTask={handleAddTask} />
      <TaskList
        tasks={tasks}
        onDragEnd={onDragEnd}
        duplicateTask={duplicateTask}
        deleteTask={deleteTask}
        currentTaskIndex={currentTaskIndex}
      />
      <Button variant="contained" color="secondary" onClick={handleExecuteTasks} sx={{ mt: 2 }}>
        Execute Tasks
      </Button>
    </Box>
  );
};

export default Actions;
